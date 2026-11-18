from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
from backend.app.indexer import index_work
from backend.app.schemas import (
    AskRequest,
    AskResponse,
    ImageIngest,
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


@app.post("/works/image", response_model=WorkResponse, status_code=201)
def create_work_from_image(payload: ImageIngest, db: Session = Depends(get_db)):
    """图片录入：调视觉模型生成描述 -> 存成作品 -> 建索引，一步完成。

    只接受服务器本机可读的路径（本地演示够用；部署时改成上传接口）。
    """
    try:
        description = describe_image(payload.image_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # 视觉服务失败：明确告诉调用方，不要静默建空作品
        raise HTTPException(status_code=502, detail=f"图片描述生成失败：{exc}") from exc

    title = payload.title or Path(payload.image_path).stem
    created = crud.create_work(
        db,
        WorkCreate(
            title=title,
            type="image",
            content=description,
            tags=payload.tags,
        ),
    )
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


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest, db: Session = Depends(get_db)):
    # 1) 向量检索：只把最相关的几个片段交给模型
    hits = vector_store.query(embed_text(req.question), top_k=RETRIEVAL_TOP_K)
    # 再按距离过滤一遍：top-k 永远会凑满 k 条，不过滤的话"引用来源"会把无关作品也算进去
    relevant = [hit for hit in hits if hit["distance"] <= MAX_DISTANCE]

    if not relevant:
        # 2) 距离太远 = 库里没有相关内容。直接拼一句回复，不调用大模型（零 token）
        titles = [w.title for w in crud.list_works(db, skip=0, limit=20)]
        if titles:
            answer = (
                f"库里没有找到与「{req.question}」相关的内容。"
                f"当前库里有 {len(titles)} 条作品：" + "、".join(titles)
            )
        else:
            answer = "作品库还是空的，先添加一些作品再问吧。"
        sources: list[int] = []
    else:
        # 3) 命中：目录 + 相关片段一起给，既能答细节也能答"库里有什么"
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
