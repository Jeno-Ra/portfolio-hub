from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from openai import (
    APIStatusError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)
from sqlalchemy.orm import Session

from backend.app import crud, llm
from backend.app.database import get_db, init_db
from backend.app.schemas import AskRequest, AskResponse, WorkCreate, WorkResponse

app = FastAPI(title="PortfolioHub API", version="0.1.0")


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
    return crud.create_work(db, work)


@app.get("/works", response_model=list[WorkResponse])
def list_works(skip: int = 0, limit: int = 10, db: Session = Depends(get_db)):
    return crud.list_works(db, skip=skip, limit=limit)


@app.get("/works/{work_id}", response_model=WorkResponse)
def get_work(work_id: int, db: Session = Depends(get_db)):
    work = crud.get_work(db, work_id)
    if work is None:
        raise HTTPException(status_code=404, detail="作品不存在")
    return work


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest, db: Session = Depends(get_db)):
    # 先调模型：失败时异常交给上方 handler，库里不会留下半截会话
    answer = llm.chat(req.question)

    conversation = crud.create_conversation(db)
    crud.add_message(db, conversation.id, "user", req.question)
    crud.add_message(db, conversation.id, "assistant", answer)

    return AskResponse(answer=answer, conversation_id=conversation.id)
