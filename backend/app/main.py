from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from backend.app import crud
from backend.app.database import get_db, init_db
from backend.app.schemas import AskRequest, AskResponse, WorkCreate, WorkResponse

app = FastAPI(title="PortfolioHub API", version="0.1.0")

#启动时确保表存在。create_all 是幂等的：缺哪张建哪张，不动已有数据
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
    conversation = crud.create_conversation(db)
    crud.add_message(db, conversation.id, "user", req.question)

    answer = f"(假答案)当前尚未接入检索，暂时无法回答：{req.question}"
    crud.add_message(db, conversation.id, "assistant", answer)

    return AskResponse(answer=answer, conversation_id=conversation.id)
