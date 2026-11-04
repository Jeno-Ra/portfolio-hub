from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import Base

#数据库文件固定在 backend/ 下，用绝对路径，不依赖启动时的工作目录
DB_PATH = Path(__file__).resolve().parent.parent / "portfolio_hub.db"
DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"

#check_same_thread=False：FastAPI 的同步路由跑在线程池里，
#SQLite 默认禁止一个连接跨线程使用
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _enable_foreign_keys(dbapi_connection, connection_record):
    """SQLite 默认不启用外键，必须每条连接单独打开，
    否则 ondelete="CASCADE" 只是写在 DDL 里好看，删父行不会级联。"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


#会话工厂：每次请求从这里拿一个 Session
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    """建表；表已存在则跳过，不会删数据"""
    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：每个请求开一个 Session，响应结束必定关闭"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
