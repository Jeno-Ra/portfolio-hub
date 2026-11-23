"""问答接口：一次提问，先判意图，再决定「检索回答」还是「直接改数据」。

两个接口共用同一套判断（`_prepare`）：
- `/ask`         普通请求/脚本用，一次性返回完整答案
- `/ask/stream`  前端用，边生成边推；管理动作和统计本来就没有增量，一次推完

两个入口分开是有原因的：EventSource 只支持 GET、不能带请求体，
所以前端必须用 POST + fetch 手动解析 SSE，而不是 EventSource。
"""

import difflib
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.app import crud, embedding, llm, policy, vector_store
from backend.app import router as intent_router
from backend.app.database import get_db
from backend.app.response import Envelope, ok
from backend.app.schemas import AskRequest, AskResponse, WorkCreate, WorkUpdate

router = APIRouter()

# 检索参数：TOP_K 决定给模型看几段；MAX_DISTANCE 是"库里没有相关内容"的判据。
# 0.45 来自实测校准：相关组 0.320–0.384，无关组 0.510–0.654，取中间偏相关侧。
RETRIEVAL_TOP_K = 5
MAX_DISTANCE = 0.45

# 最近一次检索/操作命中的作品 id。用于解析「把刚才那个的标题改成 X」这类指代。
# 单进程单用户的原型做法；多用户要改成按会话存（第 9 周）。
LAST_HITS: list[int] = []

TYPE_LABEL = {
    "video": "视频",
    "image": "图片",
    "text": "文本",
    "code": "代码",
    "project": "项目",
}


def _catalog(db: Session, limit: int = 50) -> str:
    """作品目录（只有标题/类型/标签），让模型知道"库里一共有什么"。"""
    works = crud.list_works(db, skip=0, limit=limit)
    lines = []
    for work in works:
        tags = "、".join(t.name for t in work.tags) or "无"
        lines.append(f"- [{work.type}] {work.title}｜标签：{tags}")
    return "\n".join(lines)


def _chunks_context(hits: list[dict]) -> str:
    """把命中的片段拼成带编号的上下文，编号与 sources 顺序一致，便于引用。"""
    blocks = []
    for index, hit in enumerate(hits, start=1):
        blocks.append(
            f"[{index}] 《{hit['title']}》第 {hit['chunk_index']} 段"
            f"（距离 {hit['distance']}）\n{hit['text']}"
        )
    return "\n\n".join(blocks)


def _refusal_answer(db: Session, question: str) -> str:
    """库里没有相关内容时的回复：纯代码拼装，不调用大模型（零 token）。"""
    titles = [w.title for w in crud.list_works(db, skip=0, limit=20)]
    if not titles:
        return "作品库还是空的，先添加一些作品再问吧。"
    return (
        f"库里没有找到与「{question}」相关的内容。"
        f"当前库里有 {len(titles)} 条作品：" + "、".join(titles)
    )


def _retrieve(question: str) -> list[dict]:
    """检索并按距离过滤。top-k 永远会凑满 k 条，不过滤会把无关作品也算进引用。"""
    hits = vector_store.query(embedding.embed_text(question), top_k=RETRIEVAL_TOP_K)
    return [hit for hit in hits if hit["distance"] <= MAX_DISTANCE]


def _rag_context(db: Session, relevant: list[dict]) -> str:
    """拼接送给模型的上下文：目录（知道库里有什么）+ 命中片段（答细节）。"""
    return f"【作品目录】\n{_catalog(db)}\n\n【相关片段】\n{_chunks_context(relevant)}"


def _stats_answer(db: Session) -> str:
    """统计类问题：纯 SQL 聚合，不需要模型。"""
    works = crud.list_works(db, skip=0, limit=200)
    if not works:
        return "库里还没有作品。"
    by_type: dict[str, int] = {}
    for work in works:
        by_type[work.type] = by_type.get(work.type, 0) + 1
    detail = "、".join(
        f"{TYPE_LABEL.get(kind, kind)} {count} 条"
        for kind, count in sorted(by_type.items())
    )
    return f"库里共 {len(works)} 条作品：{detail}。"


def _resolve_targets(db: Session, keyword: str) -> list:
    """按标题找作品，三档回退：

    1. 关键词是标题子串 → 直接命中
    2. 子串不中 → 用相似度模糊匹配（改过标题后，旧说法还能找到）
    3. 都没有关键词或都没匹配 → 回退到"刚才操作/检索到的那几条"
    """
    works = crud.list_works(db, skip=0, limit=200)
    if keyword:
        matched = [w for w in works if keyword in w.title]
        if matched:
            return matched

        scored = sorted(
            (
                (difflib.SequenceMatcher(None, keyword, w.title).ratio(), w)
                for w in works
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        fuzzy = [w for score, w in scored if score >= 0.5]
        if fuzzy:
            return fuzzy[:3]

    if LAST_HITS:
        by_id = {w.id: w for w in works}
        return [by_id[i] for i in LAST_HITS if i in by_id]
    return []


def _handle_management(
    db: Session, routed: dict, confirm: bool
) -> tuple[str, list[int], bool]:
    """处理 create / update / delete。

    返回 `(回复文案, 涉及的作品 id, 是否真的改了数据)`：
    最后一项目前只给前端用——只有真改了才需要重新拉列表。
    """
    intent = routed["intent"]

    if intent == "create":
        if not routed["title"]:
            hint = (
                "要新增作品的话，请把标题一起说出来，"
                "例如：记一条《茶室空镜》，内容是今天拍的素材。"
            )
            return hint, [], False
        created = crud.create_work(
            db,
            WorkCreate(
                title=routed["title"],
                type=routed.get("type") or "text",
                content=routed["content"],
                tags=routed["tags"],
            ),
        )
        message = f"已新增作品《{created.title}》（id={created.id}），索引已建好。"
        return message, [created.id], True

    targets = _resolve_targets(db, routed["target"])
    if not targets:
        hint = (
            "没找到要操作的作品。可以给我标题关键词，"
            "或者说「刚才那个」指代上一次检索到的作品。"
        )
        return hint, [], False
    if len(targets) > 1:
        names = "、".join(f"《{w.title}》(id={w.id})" for w in targets[:5])
        return f"匹配到 {len(targets)} 条作品，请说得更具体些：{names}", [], False

    work = targets[0]

    if intent == "delete":
        if not confirm:
            hint = (
                f"即将删除《{work.title}》(id={work.id})，该操作不可撤销，"
                "确认请再发一次并带上「确认删除」。"
            )
            return hint, [work.id], False
        vector_store.delete_work(work.id)
        crud.delete_work(db, work.id)
        return f"已删除《{work.title}》。", [], True

    # update：只有标题/标签/类型可改，内容由 policy 拦截
    field = routed["field"] if routed["field"] else "title"
    allowed, reason = policy.check_edit_field(field)
    if not allowed:
        return reason, [work.id], False

    field_label = policy.FIELD_LABEL[field]
    value = routed["value"]
    if not value:
        return f"要把《{work.title}》的{field_label}改成什么？", [work.id], False

    old_title = work.title

    if field == "tags":
        updated = crud.update_work(
            db, work.id, WorkUpdate(tags=[t.strip() for t in value.replace("，", ",").split(",") if t.strip()])
        )
    else:
        updated = crud.update_work(db, work.id, WorkUpdate(**{field: value}))

    if updated is None:
        return f"更新《{old_title}》失败，作品可能已被删除。", [], False

    return f"已把《{old_title}》的{field_label}改为「{value}」。", [work.id], True


def _confirmed(req: AskRequest) -> bool:
    """删除要不要真的执行。

    除了显式 `confirm: true`，文案里带「确认/确定」也算——
    前端对话框只发一句自然语言，用户照着提示回「确认删除」时不该再追问一次。
    """
    return req.confirm or any(word in req.question for word in ("确认", "确定"))


def _prepare(db: Session, req: AskRequest) -> tuple[str, list[int], list[dict], bool]:
    """统一入口：先判意图，再决定怎么答。

    返回 `(answer, sources, hits, changed)`：
    - `hits` 非空 → 需要模型生成，answer 此时为空
    - `answer` 非空 → 已经有了完整答复（统计/管理动作/拒绝话术），不用调模型
    """
    routed = intent_router.route(req.question)

    if routed["intent"] != "search":
        if routed["intent"] == "stats":
            return _stats_answer(db), [], [], False
        answer, sources, changed = _handle_management(db, routed, _confirmed(req))
        if sources:  # 让「刚才那个」也能指代刚操作过的作品
            LAST_HITS[:] = sources
        return answer, sources, [], changed

    hits = _retrieve(req.question)
    if not hits:
        LAST_HITS.clear()
        return _refusal_answer(db, req.question), [], [], False

    sources = list(dict.fromkeys(hit["work_id"] for hit in hits))
    LAST_HITS[:] = sources  # 记住"刚才那几个"，供指代解析用
    return "", sources, hits, False


def _save_exchange(db: Session, question: str, answer: str):
    """一问一答落库。成功后才写，失败不留半截会话。"""
    conversation = crud.create_conversation(db)
    crud.add_message(db, conversation.id, "user", question)
    crud.add_message(db, conversation.id, "assistant", answer)
    return conversation


@router.post("/ask", response_model=Envelope[AskResponse])
def ask(req: AskRequest, db: Session = Depends(get_db)):
    """先判意图，再分发：search 走 RAG，其余本地处理。"""
    answer, sources, hits, _ = _prepare(db, req)
    if hits:
        answer = llm.chat(req.question, _rag_context(db, hits))

    conversation = _save_exchange(db, req.question, answer)
    payload = AskResponse(
        answer=answer, conversation_id=conversation.id, sources=sources
    )
    return ok(payload, "已回答")


def _sse(event: str, payload: dict) -> str:
    """SSE 帧格式：event + data，末尾必须空行，否则前端切不出事件边界。"""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/ask/stream")
def ask_stream(req: AskRequest, db: Session = Depends(get_db)):
    """流式问答。事件顺序：meta（引用来源）→ delta（增量文本）→ done（会话 id）。

    唯一不套信封的接口：这本身就是一条事件流，外面再包一层 JSON 就没法边生成边发了。

    管理动作（改名/删除/新增）和统计没有"增量"，一次 delta 推完整句就结束——
    这样界面上说「删掉某条」也能生效，而不是只能搜。
    """
    answer, sources, hits, changed = _prepare(db, req)

    def generate():
        yield _sse("meta", {"sources": sources})

        text = answer
        if hits:
            pieces: list[str] = []
            try:
                for delta in llm.chat_stream(req.question, _rag_context(db, hits)):
                    pieces.append(delta)
                    yield _sse("delta", {"text": delta})
            except Exception as exc:  # noqa: BLE001 — 流已开始无法重试，任何异常都只能转成 error 事件
                yield _sse("error", {"message": f"生成中断：{exc}"})
                return
            text = "".join(pieces)
        else:
            yield _sse("delta", {"text": answer})

        conversation = _save_exchange(db, req.question, text)
        # refresh 告诉前端「这次真的改了数据」，让它重新拉一遍列表；
        # 纯问答不刷新，省一次请求
        yield _sse(
            "done",
            {"conversation_id": conversation.id, "sources": sources, "refresh": changed},
        )

    return StreamingResponse(generate(), media_type="text/event-stream")
