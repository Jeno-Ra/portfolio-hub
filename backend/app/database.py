from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import settings
from backend.app.models import Base

# 默认落在 backend/ 下；容器里由 compose 注入 DB_PATH=/app/data/...
# 用绝对路径，不依赖启动时的工作目录
_DEFAULT_DB = Path(__file__).resolve().parent.parent / "portfolio_hub.db"
DB_PATH = Path(settings.db_path) if settings.db_path else _DEFAULT_DB
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SQLITE_URL = f"sqlite:///{DB_PATH.as_posix()}"
# 留空 -> SQLite（本地开发）；有值 -> 用它（容器里指向 postgres 服务）
DATABASE_URL = settings.database_url or SQLITE_URL

_is_sqlite = DATABASE_URL.startswith("sqlite")

# check_same_thread 是 SQLite 专属参数，传给 Postgres 会直接 TypeError。
# pool_pre_ping：Postgres 容器重启后连接池里的旧连接会失效，
# 没有它就会报 "server closed the connection unexpectedly"。
_connect_args = {"check_same_thread": False} if _is_sqlite else {}
engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    pool_pre_ping=not _is_sqlite,
)


@event.listens_for(engine, "connect")
def _enable_foreign_keys(dbapi_connection, connection_record):
    """SQLite 默认不启用外键，必须每条连接单独打开，
    否则 ondelete="CASCADE" 只是写在 DDL 里好看，删父行不会级联。

    Postgres 原生强制外键，不需要这一步。
    """
    if not _is_sqlite:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# 会话工厂：每次请求从这里拿一个 Session
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    """建表；表已存在则跳过，不会删数据。

    本项目不再靠 ALTER 补列：模型一旦变动（works → projects/assets 那次），
    就是重建库，历史库没有迁移价值。真要长期演进，应该引入 Alembic。
    """
    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：每个请求开一个 Session，响应结束必定关闭"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
