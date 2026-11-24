"""问答接口：一次提问，先判意图，再决定「检索回答」还是「直接改数据」。

两个接口共用同一套判断（`_prepare`）：
- `/ask`         普通请求/脚本用，一次性返回完整答案
- `/ask/stream`  前端用，边生成边推；管理动作和统计本来就没有增量，一次推完

两个入口分开是有原因的：EventSource 只支持 GET、不能带请求体，
所以前端必须用 POST + fetch 手动解析 SSE，而不是 EventSource。

检索的粒度是「素材片段」，但回答里要说清它属于哪个项目 —— 用户问的是
"王者无界嬴政那个预告片"，只回"预告片"是不合格的引用。
"""

import difflib
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.app import crud, embedding, kinds, llm, policy, vector_store
from backend.app import router as intent_router
from backend.app.database import get_db
from backend.app.response import Envelope, ok
from backend.app.schemas import AskRequest, AskResponse, AssetCreate, AssetUpdate

router = APIRouter()

# 检索参数：TOP_K 决定给模型看几段；MAX_DISTANCE 是"库里没有相关内容"的判据。
# 0.45 来自实测校准：相关组 0.320–0.384，无关组 0.510–0.654，取中间偏相关侧。
RETRIEVAL_TOP_K = 5
MAX_DISTANCE = 0.45

# 最近一次检索/操作命中的素材 id。用于解析「把刚才那个的标题改成 X」这类指代。
# 单进程单用户的原型做法；多用户要改成按会话存（第 9 周）。
LAST_HITS: list[int] = []


def _catalog(db: Session, limit: int = 200) -> str:
    """项目目录：按项目分组列素材，让模型知道"哪个项目里有什么"。

    顺序和结构要稳定，否则同一问题的上下文每次都不一样，回答会飘。
    """
    projects = crud.list_projects(db, skip=0, limit=limit)
    assets = crud.list_assets(db, skip=0, limit=limit)
    if not projects and not assets:
        return "（库里还没有内容）"

    grouped: dict[int, list[str]] = {}
    for project in projects:
        grouped[project.id] = []
    loose: list[str] = []

    for asset in assets:
        label = kinds.label(asset.type)
        tags = "、".join(tag.name for tag in asset.tags) or "无"
        line = f"- [{label}] {asset.title}｜标签：{tags}"
        if asset.projects:
            for project in asset.projects:
                grouped.setdefault(project.id, []).append(line)
        else:
            loose.append(line)

    blocks: list[str] = []
    for project in projects:
        title = f"【项目】{project.title}"
        if project.summary:
            title = f"{title}：{project.summary}"
        lines = grouped.get(project.id) or []
        body = "\n".join(lines) if lines else "- （还没有素材）"
        blocks.append(f"{title}\n{body}")
    if loose:
        blocks.append("【未归入项目】\n" + "\n".join(loose))
    return "\n\n".join(blocks)


def _hit_title(hit: dict) -> str:
    """引用里的名字：能看出素材属于哪个项目才算有效引用。"""
    projects = (hit.get("projects") or "").strip()
    return f"{projects} · {hit['title']}" if projects else hit["title"]


def _chunks_context(hits: list[dict]) -> str:
    """把命中的片段拼成带编号的上下文，编号与 sources 顺序一致，便于引用。"""
    blocks = []
    for index, hit in enumerate(hits, start=1):
        blocks.append(
            f"[{index}] 《{_hit_title(hit)}》第 {hit['chunk_index']} 段"
            f"（距离 {hit['distance']}）\n{hit['text']}"
        )
    return "\n\n".join(blocks)


def _refusal_answer(db: Session, question: str) -> str:
    """库里没有相关内容时的回复：纯代码拼装，不调用大模型（零 token）。"""
    projects = crud.list_projects(db, skip=0, limit=20)
    assets = crud.list_assets(db, skip=0, limit=20)
    if not projects and not assets:
        return "作品库还是空的，先建一个项目、往里放点素材再问吧。"
    names = "、".join(project.title for project in projects) or "（没有项目）"
    return (
        f"库里没有找到与「{question}」相关的内容。"
        f"当前有 {len(projects)} 个项目：{names}；素材共 {len(assets)} 件。"
    )


def _retrieve(question: str) -> list[dict]:
    """问题向量化 → Chroma top-k → 距离过滤。

    top-k 只表示“最接近的候选”，不代表一定相关；所以必须再过 MAX_DISTANCE。
    否则用户问天气时，系统也会硬塞 5 件最不相关的素材给模型，增加幻觉风险。
    """
    hits = vector_store.query(embedding.embed_text(question), top_k=RETRIEVAL_TOP_K)
    return [hit for hit in hits if hit["distance"] <= MAX_DISTANCE]


def _rag_context(db: Session, relevant: list[dict]) -> str:
    """拼接送给模型的上下文：目录（知道库里有什么）+ 命中片段（答细节）。"""
    return f"【项目与素材】\n{_catalog(db)}\n\n【相关片段】\n{_chunks_context(relevant)}"


def _stats_answer(db: Session) -> str:
    """统计类问题：纯 SQL 聚合，不需要模型。"""
    projects = crud.list_projects(db, skip=0, limit=200)
    assets = crud.list_assets(db, skip=0, limit=200)
    if not projects and not assets:
        return "库里还没有项目，也没有素材。"

    by_kind: dict[str, int] = {}
    for asset in assets:
        by_kind[asset.type] = by_kind.get(asset.type, 0) + 1
    detail = "、".join(
        f"{kinds.label(kind)} {count} 件" for kind, count in sorted(by_kind.items())
    )
    head = f"库里共 {len(projects)} 个项目、{len(assets)} 件素材"
    return f"{head}：{detail}。" if detail else f"{head}。"


def _resolve_targets(db: Session, keyword: str) -> list:
    """按标题找素材，三档回退：

    1. 关键词是标题子串 → 直接命中
    2. 子串不中 → 用相似度模糊匹配（改过标题后，旧说法还能找到）
    3. 都没有关键词或都没匹配 → 回退到"刚才操作/检索到的那几条"
    """
    assets = crud.list_assets(db, skip=0, limit=200)
    if keyword:
        matched = [a for a in assets if keyword in a.title]
        if matched:
            return matched

        scored = sorted(
            (
                (difflib.SequenceMatcher(None, keyword, a.title).ratio(), a)
                for a in assets
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        fuzzy = [a for score, a in scored if score >= 0.5]
        if fuzzy:
            return fuzzy[:3]

    if LAST_HITS:
        by_id = {a.id: a for a in assets}
        return [by_id[i] for i in LAST_HITS if i in by_id]
    return []


def _handle_management(
    db: Session, routed: dict, confirm: bool
) -> tuple[str, list[int], bool]:
    """处理 create / update / delete。

    返回 `(回复文案, 涉及的素材 id, 是否真的改了数据)`：
    最后一项给前端用——只有真改了才需要重新拉列表。
    """
    intent = routed["intent"]

    if intent == "create":
        if not routed["title"]:
            hint = (
                "要新增素材的话，请把标题一起说出来，"
                "例如：记一条《茶室空镜》，内容是今天拍的素材。"
            )
            return hint, [], False
        created = crud.create_asset(
            db,
            AssetCreate(
                title=routed["title"],
                # 聊天里没说要什么类型就留空（未分类），不要替用户猜一个
                type=routed.get("type") or "",
                content=routed["content"],
                tags=routed["tags"],
            ),
        )
        message = (
            f"已新增素材《{created.title}》（id={created.id}），索引已建好。"
            "要归到哪个项目下，可以在项目详情里挂上。"
        )
        return message, [created.id], True

    targets = _resolve_targets(db, routed["target"])
    if not targets:
        hint = (
            "没找到要操作的素材。可以给我标题关键词，"
            "或者说「刚才那个」指代上一次检索到的素材。"
        )
        return hint, [], False
    if len(targets) > 1:
        names = "、".join(f"《{a.title}》(id={a.id})" for a in targets[:5])
        return f"匹配到 {len(targets)} 件素材，请说得更具体些：{names}", [], False

    asset = targets[0]

    if intent == "delete":
        if not confirm:
            hint = (
                f"即将删除《{asset.title}》(id={asset.id})，该操作不可撤销，"
                "确认请再发一次并带上「确认删除」。"
            )
            return hint, [asset.id], False
        vector_store.delete_asset(asset.id)
        crud.delete_asset(db, asset.id)
        return f"已删除《{asset.title}》。", [], True

    # update：只有标题/标签/类型可改，内容由 policy 拦截
    field = routed["field"] if routed["field"] else "title"
    allowed, reason = policy.check_edit_field(field)
    if not allowed:
        return reason, [asset.id], False

    field_label = policy.FIELD_LABEL[field]
    value = routed["value"]
    if not value:
        return f"要把《{asset.title}》的{field_label}改成什么？", [asset.id], False

    old_title = asset.title

    if field == "tags":
        updated = crud.update_asset(
            db,
            asset.id,
            AssetUpdate(
                tags=[t.strip() for t in value.replace("，", ",").split(",") if t.strip()]
            ),
        )
    else:
        updated = crud.update_asset(db, asset.id, AssetUpdate(**{field: value}))

    if updated is None:
        return f"更新《{old_title}》失败，素材可能已被删除。", [], False

    return f"已把《{old_title}》的{field_label}改为「{value}」。", [asset.id], True


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
        if sources:  # 让「刚才那个」也能指代刚操作过的素材
            LAST_HITS[:] = sources
        return answer, sources, [], changed

    hits = _retrieve(req.question)
    if not hits:
        LAST_HITS.clear()
        return _refusal_answer(db, req.question), [], [], False

    sources = list(dict.fromkeys(hit["asset_id"] for hit in hits))
    LAST_HITS[:] = sources  # 记住"刚才那几个"，供指代解析用
    return "", sources, hits, False


def _source_refs(hits: list[dict]) -> list[dict]:
    """命中片段 → 引用条目（第几号 + 素材 + 所属项目）。

    界面要显示「[1] 《项目 · 素材》」，这些信息本来就在命中结果里，
    与其让前端再查一遍，不如一次给全。同一件素材的多个片段只算一条引用。
    """
    refs: list[dict] = []
    seen: set[int] = set()
    for hit in hits:
        asset_id = hit["asset_id"]
        if asset_id in seen:
            continue
        seen.add(asset_id)
        refs.append(
            {
                "index": len(refs) + 1,
                "asset_id": asset_id,
                "title": hit.get("title", ""),
                "project": hit.get("projects", ""),
            }
        )
    return refs

def _save_exchange(db: Session, question: str, answer: str):
    """一问一答落库。成功后才写，失败不留半截会话。"""
    conversation = crud.create_conversation(db)
    crud.add_message(db, conversation.id, "user", question)
    crud.add_message(db, conversation.id, "assistant", answer)
    return conversation


@router.post("/ask", response_model=Envelope[AskResponse])
def ask(req: AskRequest, db: Session = Depends(get_db)):
    """先判意图，再分发：search 走 RAG，其余本地处理。"""
    answer, _sources, hits, _ = _prepare(db, req)
    if hits:
        answer = llm.chat(req.question, _rag_context(db, hits))

    conversation = _save_exchange(db, req.question, answer)
    payload = AskResponse(
        answer=answer, conversation_id=conversation.id, sources=_source_refs(hits)
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
    answer, _sources, hits, changed = _prepare(db, req)

    def generate():
        yield _sse("meta", {"sources": _source_refs(hits)})

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
            {"conversation_id": conversation.id, "sources": _source_refs(hits), "refresh": changed},
        )

    return StreamingResponse(generate(), media_type="text/event-stream")
