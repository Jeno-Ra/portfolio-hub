import json
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from openai import (
    APIStatusError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)
from sqlalchemy.orm import Session

from backend.app import crud, llm, vector_store
from backend.app.database import get_db, init_db
from backend.app.embedding import embed_text
from backend.app.files import extract_text
from backend.app.indexer import index_work
from backend.app.schemas import (
    AskRequest,
    AskResponse,
    IngestRequest,
    WorkCreate,
    WorkResponse,
    WorkUpdate,
)
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


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/works", response_model=WorkResponse, status_code=201)
def create_work(work: WorkCreate, db: Session = Depends(get_db)):
    created = crud.create_work(db, work)
    # 入库后立刻建索引：否则新作品搜不到（之前就是这个坑）
    index_work(db, created)
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


@app.post("/works/ingest", response_model=WorkResponse, status_code=201)
def ingest_work(payload: IngestRequest, db: Session = Depends(get_db)):
    """统一录入入口：按类型决定「内容」从哪来。

    图片 → 视觉模型生成描述
    视频 → 只存元数据（手填 notes），给了封面则额外描述封面画面
    PDF/文本/代码 → 解析正文
    """
    path = Path(payload.path)
    work_type = payload.type or _infer_type(path)
    title = payload.title or path.stem
    content = (payload.notes or "").strip()

    try:
        if work_type == "image":
            content = describe_image(path)
        elif work_type == "video":
            # 规划里的边界：视频不做抽帧/转写，只记元数据
            if payload.cover_path:
                content = (
                    f"{content}\n封面画面：{describe_image(payload.cover_path)}".strip()
                )
            if not content:
                content = "（暂无描述）"
        else:
            content = extract_text(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        # 不支持的类型 / 扫描版 PDF / 解码失败，都是调用方的问题
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"文件解析失败：{exc}") from exc

    created = crud.create_work(
        db,
        WorkCreate(title=title, type=work_type, content=content, tags=payload.tags),
    )
    created.source_path = str(path)
    db.commit()
    db.refresh(created)
    index_work(db, created)
    return created


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
    index_work(db, work)
    return work


@app.delete("/works/{work_id}", status_code=204)
def remove_work(work_id: int, db: Session = Depends(get_db)):
    if not crud.delete_work(db, work_id):
        raise HTTPException(status_code=404, detail="作品不存在")
    vector_store.delete_work(work_id)  # 同步清掉向量，否则残留脏数据


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


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest, db: Session = Depends(get_db)):
    # 1) 向量检索：只把最相关的几个片段交给模型
    hits = vector_store.query(embed_text(req.question), top_k=RETRIEVAL_TOP_K)
    # 再按距离过滤一遍：top-k 永远会凑满 k 条，不过滤的话"引用来源"会把无关作品也算进去
    relevant = [hit for hit in hits if hit["distance"] <= MAX_DISTANCE]

    if not relevant:
        answer = _refusal_answer(db, req.question)
        sources: list[int] = []
    else:
        # 2) 命中：目录 + 相关片段一起给，既能答细节也能答"库里有什么"
        context = (
            f"【作品目录】\n{_catalog(db)}\n\n【相关片段】\n{_chunks_context(relevant)}"
        )
        answer = llm.chat(req.question, context)
        sources = list(dict.fromkeys(hit["work_id"] for hit in relevant))

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
    hits = vector_store.query(embed_text(req.question), top_k=RETRIEVAL_TOP_K)
    relevant = [hit for hit in hits if hit["distance"] <= MAX_DISTANCE]
    sources = list(dict.fromkeys(hit["work_id"] for hit in relevant))

    def generate():
        if not relevant:
            # 没有命中：拒绝文案当作一个 delta 发出去，不调用模型
            answer = _refusal_answer(db, req.question)
            yield _sse("meta", {"sources": []})
            yield _sse("delta", {"text": answer})
        else:
            context = f"【作品目录】\n{_catalog(db)}\n\n【相关片段】\n{_chunks_context(relevant)}"
            yield _sse("meta", {"sources": sources})
            pieces: list[str] = []
            try:
                for delta in llm.chat_stream(req.question, context):
                    pieces.append(delta)
                    yield _sse("delta", {"text": delta})
            except Exception as exc:  # 流已开始，无法重试，只能明确报错
                yield _sse("error", {"message": f"生成中断：{exc}"})
                return
            answer = "".join(pieces)

        # 完整答案落库（与 /ask 一致：成功后才写，失败不留半截会话）
        conversation = crud.create_conversation(db)
        crud.add_message(db, conversation.id, "user", req.question)
        crud.add_message(db, conversation.id, "assistant", answer)
        yield _sse("done", {"conversation_id": conversation.id, "sources": sources})

    return StreamingResponse(generate(), media_type="text/event-stream")
