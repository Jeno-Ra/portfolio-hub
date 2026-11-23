import difflib
import json
import os
import sys
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import (
    APIStatusError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)
from sqlalchemy.orm import Session

from backend.app import (
    crud,
    embedding,
    errors,
    llm,
    policy,
    router,
    user_config,
    vector_store,
    vision,
)
from backend.app.config import IS_FROZEN, settings
from backend.app.database import get_db, init_db
from backend.app.embedding import embed_text
from backend.app.files import extract_text
from backend.app.indexer import index_work
from backend.app.models import Work
from backend.app.schemas import (
    AskRequest,
    AskResponse,
    IngestRequest,
    SettingsUpdate,
    WorkCreate,
    WorkResponse,
    WorkUpdate,
)
from backend.app.uploads import is_media_file, safe_name, save_upload
from backend.app.vision import describe_image

app = FastAPI(title="PortfolioHub API", version="0.1.0")

# 检索参数：TOP_K 决定给模型看几段；MAX_DISTANCE 是"库里没有相关内容"的判据。
# 0.45 来自实测校准：相关组 0.320–0.384，无关组 0.510–0.654，取中间偏相关侧。
RETRIEVAL_TOP_K = 5
MAX_DISTANCE = 0.45

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发期放开；部署后收紧成具体前端域名
    allow_credentials=False,  # 与 allow_origins=["*"] 不能同时为 True
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def fresh_frontend(request: Request, call_next):
    """HTML 每次都回源校验。

    前端是 StaticFiles 直接吐出去的，浏览器会把 index.html 也缓存住；
    改完界面再点「新增」还是旧 JS，表现就是"点了没反应"。这里让 HTML 每次
    都重新验证，静态资源则靠 index.html 里的 ?v= 版本号打破缓存。
    """
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


@app.exception_handler(errors.NotConfigured)
async def not_configured_handler(request: Request, exc: errors.NotConfigured):
    """缺密钥属于「用户还没配置」，不是服务器故障：400 + 可读提示，前端据此引导去设置。"""
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(APIStatusError)
async def upstream_error_handler(request: Request, exc: APIStatusError):
    """把上游大模型服务的错误翻译成合适的 HTTP 语义。

    Starlette 沿异常类的 MRO 查找处理器，注册在 APIStatusError 上即可
    覆盖 AuthenticationError / RateLimitError 等全部子类。
    """
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        status, detail = 502, "上游模型服务鉴权失败，请检查 DEEPSEEK_API_KEY"
    elif isinstance(exc, RateLimitError):
        status, detail = 429, "上游模型服务限流，请稍后重试"
    else:
        status, detail = 502, f"上游模型服务返回错误 {exc.status_code}"

    return JSONResponse(status_code=status, content={"detail": detail})


# 启动时确保表存在。create_all 是幂等的：缺哪张建哪张，不动已有数据
init_db()

# 打包版没有 .env：密钥存在用户目录的 设置.json 里，启动时灌进 settings
user_config.apply()


def _index_quietly(db: Session, work: Work) -> None:
    """建索引，但没配向量化密钥时不阻塞主流程。

    打包版第一次运行往往还没有密钥：这时作品要能先存下来，
    等用户在设置里填好密钥、保存时再统一补建（见 update_settings）。
    """
    try:
        index_work(db, work)
    except errors.NotConfigured:
        pass



@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/works", response_model=WorkResponse, status_code=201)
def create_work(work: WorkCreate, db: Session = Depends(get_db)):
    created = crud.create_work(db, work)
    # 入库后立刻建索引：否则新作品搜不到（之前就是这个坑）
    _index_quietly(db, created)
    return created


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv"}
CODE_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".cpp",
    ".c",
    ".go",
    ".rs",
    ".sql",
    ".html",
    ".css",
    ".sh",
}


def _infer_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in CODE_SUFFIXES:
        return "code"
    return "text"


def _content_for(
    work_type: str, path: Path, notes: str, cover_path: Path | None = None
) -> str:
    """按类型产出「作品内容」。

    图片 → 视觉模型生成描述
    视频 → 只存元数据（手填 notes），给了封面则额外描述封面画面
    其余 → 解析正文（PDF / Word / 表格 / 代码 / 纯文本），手填 notes 拼在最前面
    """
    try:
        if work_type == "image":
            return describe_image(path)
        if work_type == "video":
            content = notes.strip()
            if cover_path:
                content = f"{content}\n封面画面：{describe_image(cover_path)}".strip()
            return content or "（暂无描述）"
        text = extract_text(path)
        note = notes.strip()
        return f"{note}\n\n{text}" if note else text
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        # 不支持的类型 / 扫描版 PDF / 解码失败，都是调用方的问题
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"文件解析失败：{exc}") from exc


def _split_tags(raw: str) -> list[str]:
    """逗号分隔的标签串 → 列表，中英文逗号都认。"""
    return [part.strip() for part in raw.replace("，", ",").split(",") if part.strip()]


def _store_work(
    db: Session, *, title: str, work_type: str, content: str, tags: list[str], source: Path
) -> Work:
    created = crud.create_work(
        db, WorkCreate(title=title, type=work_type, content=content, tags=tags)
    )
    created.source_path = str(source)
    db.commit()
    db.refresh(created)
    _index_quietly(db, created)
    return created


@app.post("/works/upload", response_model=WorkResponse, status_code=201)
def upload_work(
    file: UploadFile = File(...),
    cover: UploadFile | None = File(None),
    title: str = Form(""),
    type: str = Form(""),
    tags: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    """浏览器 / 手机上传录入：文件先落盘到 media/，再按类型解析。

    为什么网页必须走这条：`<input type=file>` 只给文件内容，不给本地绝对路径，
    所以"填路径"那种方式在浏览器里根本不成立。`/works/ingest` 留给服务器端场景
    （docker cp 放进去的文件、批量脚本），两边解析逻辑是同一个 `_content_for`。
    """
    original = safe_name(file.filename or "")
    try:
        saved = save_upload(file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    saved_cover: Path | None = None
    try:
        if cover is not None and cover.filename:
            saved_cover = save_upload(cover)
        work_type = type or _infer_type(saved)
        content = _content_for(work_type, saved, notes, saved_cover)
    except HTTPException:
        # 解析失败就别留下垃圾文件
        saved.unlink(missing_ok=True)
        if saved_cover:
            saved_cover.unlink(missing_ok=True)
        raise

    return _store_work(
        db,
        title=title.strip() or Path(original).stem,
        work_type=work_type,
        content=content,
        tags=_split_tags(tags),
        source=saved,
    )


@app.post("/works/ingest", response_model=WorkResponse, status_code=201)
def ingest_work(payload: IngestRequest, db: Session = Depends(get_db)):
    """按路径录入：文件已经在这台机器上时用（服务器/脚本），浏览器用 `/works/upload`。"""
    path = Path(payload.path)
    work_type = payload.type or _infer_type(path)
    cover_path = Path(payload.cover_path) if payload.cover_path else None
    content = _content_for(work_type, path, payload.notes or "", cover_path)

    return _store_work(
        db,
        title=payload.title or path.stem,
        work_type=work_type,
        content=content,
        tags=payload.tags,
        source=path,
    )



@app.get("/works", response_model=list[WorkResponse])
def list_works(skip: int = 0, limit: int = 10, db: Session = Depends(get_db)):
    return crud.list_works(db, skip=skip, limit=limit)


@app.get("/works/{work_id}", response_model=WorkResponse)
def get_work(work_id: int, db: Session = Depends(get_db)):
    work = crud.get_work(db, work_id)
    if work is None:
        raise HTTPException(status_code=404, detail="作品不存在")
    return work


@app.patch("/works/{work_id}", response_model=WorkResponse)
def patch_work(work_id: int, data: WorkUpdate, db: Session = Depends(get_db)):
    work = crud.update_work(db, work_id, data)
    if work is None:
        raise HTTPException(status_code=404, detail="作品不存在")
    # 内容变了要重建索引，否则检索还在命中旧片段
    _index_quietly(db, work)
    return work


@app.delete("/works/{work_id}", status_code=204)
def remove_work(work_id: int, db: Session = Depends(get_db)):
    work = crud.get_work(db, work_id)
    if work is None:
        raise HTTPException(status_code=404, detail="作品不存在")

    source = Path(work.source_path) if work.source_path else None
    crud.delete_work(db, work_id)
    vector_store.delete_work(work_id)  # 同步清掉向量，否则残留脏数据

    # 只清 media/ 里我们自己存的那份副本；用户原始文件永远不碰
    if source is not None and is_media_file(source):
        source.unlink(missing_ok=True)


def _catalog(db: Session, limit: int = 50) -> str:
    """作品目录（只有标题/类型/标签），让模型知道"库里一共有什么"。"""
    works = crud.list_works(db, skip=0, limit=limit)
    lines = []
    for w in works:
        tags = "、".join(t.name for t in w.tags) or "无"
        lines.append(f"- [{w.type}] {w.title}｜标签：{tags}")
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
    hits = vector_store.query(embed_text(question), top_k=RETRIEVAL_TOP_K)
    return [hit for hit in hits if hit["distance"] <= MAX_DISTANCE]


def _rag_context(db: Session, relevant: list[dict]) -> str:
    """拼接送给模型的上下文：目录（知道库里有什么）+ 命中片段（答细节）。"""
    return f"【作品目录】\n{_catalog(db)}\n\n【相关片段】\n{_chunks_context(relevant)}"


# 最近一次检索命中的作品 id。用于解析「把刚才那个的标题改成 X」这类指代。
# 单进程单用户的原型做法；多用户要改成按会话存（第 9 周）。
LAST_HITS: list[int] = []

TYPE_LABEL = {
    "video": "视频",
    "image": "图片",
    "text": "文本",
    "code": "代码",
    "project": "项目",
}


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
) -> tuple[str, list[int]]:
    """处理 create / update / delete，返回 (回复文案, 涉及的作品 id)。"""
    intent = routed["intent"]

    if intent == "create":
        if not routed["title"]:
            return (
                (
                    "要新增作品的话，请把标题一起说出来，"
                    "例如：记一条《茶室空镜》，内容是今天拍的素材。"
                ),
                [],
            )
        created = crud.create_work(
            db,
            WorkCreate(
                title=routed["title"],
                type=routed.get("type") or "text",
                content=routed["content"],
                tags=routed["tags"],
            ),
        )
        _index_quietly(db, created)
        return f"已新增作品《{created.title}》（id={created.id}），索引已建好。", [
            created.id
        ]

    targets = _resolve_targets(db, routed["target"])
    if not targets:
        return (
            (
                "没找到要操作的作品。可以给我标题关键词，"
                "或者说「刚才那个」指代上一次检索到的作品。"
            ),
            [],
        )
    if len(targets) > 1:
        names = "、".join(f"《{w.title}》(id={w.id})" for w in targets[:5])
        return f"匹配到 {len(targets)} 条作品，请说得更具体些：{names}", []

    work = targets[0]

    if intent == "delete":
        if not confirm:
            return (
                (
                    f"即将删除《{work.title}》(id={work.id})，该操作不可撤销，"
                    f"确认请再发一次并带上「确认删除」。"
                ),
                [work.id],
            )
        vector_store.delete_work(work.id)
        crud.delete_work(db, work.id)
        return f"已删除《{work.title}》。", []

    # update：只有标题/标签/类型可改，内容由 policy 拦截
    field = routed["field"] if routed["field"] else "title"
    allowed, reason = policy.check_edit_field(field)
    if not allowed:
        return reason, [work.id]

    field_label = policy.FIELD_LABEL[field]
    value = routed["value"]
    if not value:
        return f"要把《{work.title}》的{field_label}改成什么？", [work.id]

    old_title = work.title

    if field == "tags":
        tags = [t.strip() for t in value.replace("，", ",").split(",") if t.strip()]
        updated = crud.update_work(db, work.id, WorkUpdate(tags=tags))
    else:
        updated = crud.update_work(db, work.id, WorkUpdate(**{field: value}))

    if updated is None:
        return f"更新《{old_title}》失败，作品可能已被删除。", []

    _index_quietly(db, updated)  # 抬头/标签变了要重建索引，否则检索命中旧元数据
    return f"已把《{old_title}》的{field_label}改为「{value}」。", [work.id]


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest, db: Session = Depends(get_db)):
    """先判意图，再分发：search 走 RAG，其余走管理动作。"""
    routed = router.route(req.question)
    intent = routed["intent"]

    if intent == "stats":
        answer, sources = _stats_answer(db), []
    elif intent in {"create", "update", "delete"}:
        answer, sources = _handle_management(db, routed, req.confirm)
        if sources:  # 让「刚才那个」也能指代刚操作过的作品
            LAST_HITS[:] = sources
    else:
        relevant = _retrieve(req.question)
        if not relevant:
            answer, sources = _refusal_answer(db, req.question), []
            LAST_HITS.clear()
        else:
            answer = llm.chat(req.question, _rag_context(db, relevant))
            sources = list(dict.fromkeys(hit["work_id"] for hit in relevant))
            LAST_HITS[:] = sources  # 记住"刚才那几个"，供指代解析用

    conversation = crud.create_conversation(db)
    crud.add_message(db, conversation.id, "user", req.question)
    crud.add_message(db, conversation.id, "assistant", answer)

    return AskResponse(
        answer=answer,
        conversation_id=conversation.id,
        sources=sources,
    )


def _sse(event: str, payload: dict) -> str:
    """SSE 帧格式：event + data，末尾必须空行，否则前端切不出事件边界。"""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/ask/stream")
def ask_stream(req: AskRequest, db: Session = Depends(get_db)):
    """流式问答。事件顺序：meta（引用来源）→ delta（增量文本）→ done（会话 id）。

    说明：EventSource 只支持 GET 且不能带请求体，所以前端用 fetch + ReadableStream
    自己解析 SSE，而不是用浏览器的 EventSource。
    """
    relevant = _retrieve(req.question)
    sources = list(dict.fromkeys(hit["work_id"] for hit in relevant))

    def generate():
        if not relevant:
            # 没有命中：拒绝文案当作一个 delta 发出去，不调用模型
            answer = _refusal_answer(db, req.question)
            yield _sse("meta", {"sources": []})
            yield _sse("delta", {"text": answer})
        else:
            context = _rag_context(db, relevant)
            yield _sse("meta", {"sources": sources})
            pieces: list[str] = []
            try:
                for delta in llm.chat_stream(req.question, context):
                    pieces.append(delta)
                    yield _sse("delta", {"text": delta})
            except Exception as exc:  # noqa: BLE001 — 流已开始无法重试，任何异常都只能转成 error 事件
                yield _sse("error", {"message": f"生成中断：{exc}"})
                return
            answer = "".join(pieces)

        # 完整答案落库（与 /ask 一致：成功后才写，失败不留半截会话）
        conversation = crud.create_conversation(db)
        crud.add_message(db, conversation.id, "user", req.question)
        crud.add_message(db, conversation.id, "assistant", answer)
        yield _sse("done", {"conversation_id": conversation.id, "sources": sources})

    return StreamingResponse(generate(), media_type="text/event-stream")


# ---- 设置（给人用的，不是给 AI 用的）--------------------------------------
# 打包版没有 .env，密钥由用户在界面里填，存到用户目录的 设置.json。


def _settings_payload() -> dict:
    return {
        "configured": user_config.configured(),
        "keys": user_config.masked(),
        "labels": user_config.LABELS,
        "storage": user_config.storage_hint(),
        # 打包版才给「退出程序」按钮：容器/开发环境里退出没有意义
        "portable": IS_FROZEN,
    }


@app.get("/settings")
def read_settings():
    return _settings_payload()


@app.put("/settings")
def update_settings(payload: SettingsUpdate, db: Session = Depends(get_db)):
    """保存密钥 → 立刻生效 → 顺手把之前没索引成功的作品补上。

    补索引放在这里而不是后台任务：作品通常只有几条，同步做完再返回，
    用户看到「已为 N 条作品建好索引」比等一个看不见的后台任务更安心。
    """
    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="没有要保存的字段")

    user_config.save(updates)
    user_config.apply()

    return {**_settings_payload(), "indexed_works": _reindex_all(db)}


@app.post("/settings/test")
def test_settings(payload: SettingsUpdate):
    """逐个服务探活：用户填错哪把钥匙，一眼看得出来。

    三个探测都是「不消耗生成额度」的调用（列模型 / 一次极短向量化），
    所以点几次也不心疼。
    """
    probes = {
        "deepseek_api_key": ("DeepSeek（对话）", llm.ping),
        "embedding_api_key": ("硅基流动（向量化）", embedding.ping),
        "gpt_api_key": ("视觉模型（图片描述）", vision.ping),
    }
    provided = payload.model_dump(exclude_none=True)
    results: list[dict] = []

    for key, (label, probe) in probes.items():
        if key in provided:
            # 先临时灌进去再探活，失败要还原，避免把没验证过的密钥留在内存里
            original = getattr(settings, key)
            setattr(settings, key, str(provided[key] or "").strip())
            _reload_clients()
            try:
                probe()
                results.append({"service": label, "ok": True, "message": "连接正常"})
            except errors.NotConfigured:
                results.append({"service": label, "ok": False, "message": "没有填密钥"})
            except Exception as exc:  # noqa: BLE001 — 探活要如实回报任何失败原因
                results.append({"service": label, "ok": False, "message": str(exc)[:200]})
            finally:
                setattr(settings, key, original)
                _reload_clients()
        elif getattr(settings, key):
            try:
                probe()
                results.append({"service": label, "ok": True, "message": "连接正常"})
            except Exception as exc:  # noqa: BLE001 — 同上
                results.append({"service": label, "ok": False, "message": str(exc)[:200]})
        else:
            results.append({"service": label, "ok": False, "message": "还没有填"})

    return {"results": results}


def _reload_clients() -> None:
    llm.reload()
    embedding.reload()
    vision.reload()


def _reindex_all(db: Session) -> int:
    """把库里所有作品重建索引，返回成功的条数。

    没配向量化密钥时直接返回 0，不报错——用户可能只填了对话密钥。
    """
    if not settings.embedding_api_key:
        return 0

    done = 0
    for work in db.query(Work).all():
        try:
            index_work(db, work)
            done += 1
        except (errors.NotConfigured, RuntimeError):
            break  # 密钥/网络有问题就停，别对着每条作品重复失败
    return done


@app.post("/quit")
def quit_app():
    """打包版专用：关窗口不会结束进程，需要一个显式的退出口。

    延迟 0.3 秒再退出，是为了先把 HTTP 200 发回去——否则前端只会看到「连接被重置」。
    """
    if not IS_FROZEN:
        raise HTTPException(status_code=400, detail="只有打包版支持退出")

    threading.Timer(0.3, lambda: os._exit(0)).start()
    return {"ok": True}


# 前端静态托管。必须放在所有 API 路由之后：
# FastAPI 先匹配已注册的路由，匹配不到才落到这个 mount，
# 所以 /works、/ask、/docs 不会被它吃掉。
#
# 为什么同源托管：手机上访问时，"127.0.0.1" 指的是手机自己而不是电脑。
# 前后端同源后前端用相对路径请求，一个地址（如 http://<电脑IP>:8000）就够，
# 也不用再配 CORS。
if IS_FROZEN:
    # PyInstaller 把 frontend 解到临时目录（sys._MEIPASS），不再是仓库相对位置
    FRONTEND_DIR = Path(getattr(sys, "_MEIPASS", ".")) / "frontend"
else:
    FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
