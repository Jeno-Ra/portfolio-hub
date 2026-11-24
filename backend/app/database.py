from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event, text
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

    create_all 仅新增缺失表，不修改已有列。分组使用独立表增量升级，
    并为旧项目素材关联补根目录记录；未来列变更需要显式迁移，禁止重建用户库。
    """
    Base.metadata.create_all(bind=engine)
    # 增量迁移：为既有项目-素材关联补充分组表根节点记录；不删除旧数据
    with engine.begin() as conn:
        if _is_sqlite:
            conn.execute(text("""
                INSERT INTO project_asset_groups(project_id, asset_id, group_id)
                SELECT pa.project_id, pa.asset_id, NULL FROM project_assets pa
                WHERE NOT EXISTS (
                    SELECT 1 FROM project_asset_groups pag
                    WHERE pag.project_id = pa.project_id AND pag.asset_id = pa.asset_id
                )
            """))
        else:
            conn.execute(text("""
                INSERT INTO project_asset_groups(project_id, asset_id, group_id)
                SELECT pa.project_id, pa.asset_id, NULL FROM project_assets pa
                ON CONFLICT (project_id, asset_id) DO NOTHING
            """))


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：每个请求开一个 Session，响应结束必定关闭"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
