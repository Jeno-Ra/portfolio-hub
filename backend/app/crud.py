"""数据访问层：所有对数据库的读写都收在这里，
main.py 只负责收发 HTTP，不直接写 SQL / 不直接操作 ORM 对象。"""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app import models, schemas


def _get_or_create_tags(db: Session, names: list[str]) -> list[models.Tag]:
    """按名字拿标签，没有就新建。同一个请求里重复的名字只算一次。"""
    if not names:
        return []

    # 一次查询把已存在的都捞出来，避免逐个 SELECT
    stmt = select(models.Tag).where(models.Tag.name.in_(names))
    existing = {tag.name: tag for tag in db.scalars(stmt)}

    result: list[models.Tag] = []
    for name in dict.fromkeys(names):  # dict.fromkeys 去重且保留顺序
        tag = existing.get(name)
        if tag is None:
            tag = models.Tag(name=name)
            db.add(tag)
        result.append(tag)
    return result


def create_work(db: Session, data: schemas.WorkCreate) -> models.Work:
    """新增作品。tags 是字符串列表，不能直接塞进 Work()，要转成 Tag 对象。"""
    work = models.Work(
        title=data.title,
        type=data.type,
        content=data.content,
    )
    work.tags = _get_or_create_tags(db, data.tags)
    db.add(work)
    db.commit()
    db.refresh(work)  # commit 之后才会拿到自增 id 和 created_at
    return work


def list_works(db: Session, skip: int = 0, limit: int = 10) -> list[models.Work]:
    """分页列出作品。必须显式 order_by，否则 SQL 不保证顺序，分页会漏行/重行。"""
    stmt = (
        select(models.Work)
        .options(selectinload(models.Work.tags))  # 一次性把标签捞出来，避免每行一次查询
        .order_by(models.Work.id)
        .offset(skip)
        .limit(limit)
    )
    return list(db.scalars(stmt))


def get_work(db: Session, work_id: int) -> models.Work | None:
    """按主键查单个作品；查不到返回 None，要不要报 404 由接口层决定"""
    return db.get(
        models.Work,
        work_id,
        options=[selectinload(models.Work.tags)],
    )


def update_work(
    db: Session, work_id: int, data: schemas.WorkUpdate
) -> models.Work | None:
    """部分更新。只处理客户端真正传了的字段（exclude_unset），
    否则 PATCH 会退化成"把没传的字段全部清空"。"""
    work = get_work(db, work_id)
    if work is None:
        return None

    payload = data.model_dump(exclude_unset=True)
    for field, value in payload.items():
        if field == "tags":
            work.tags = _get_or_create_tags(db, value)
        else:
            setattr(work, field, value)

    db.commit()
    db.refresh(work)
    return work


def delete_work(db: Session, work_id: int) -> bool:
    """删除作品，返回是否真的删掉（接口层据此决定 204 还是 404）。
    切片与标签关联由关系上的 cascade 一并清理。"""
    work = get_work(db, work_id)
    if work is None:
        return False
    db.delete(work)
    db.commit()
    return True


def create_conversation(db: Session, title: str = "新对话") -> models.Conversation:
    conversation = models.Conversation(title=title)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def add_message(
    db: Session,
    conversation_id: int,
    role: str,
    content: str,
) -> models.Message:
    """往会话里追加一条消息。role 只能是 user / assistant。"""
    message = models.Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message
