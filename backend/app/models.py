#ORM 数据模型：SQLAlchemy 2.x 声明式写法
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """所有模型的基类"""


#多对多关联表：作品<-->标签
#只有两个外键、没有业务字段，所以用 Table 而不是建类
work_tags = Table(
    "work_tags",
    Base.metadata,
    Column("work_id", ForeignKey("works.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Work(Base):
    """作品主表：所有模态共用一张表，靠 type 区分"""

    __tablename__ = "works"

    id: Mapped[int] = mapped_column(primary_key=True)  #主键，自增
    title: Mapped[str] = mapped_column(String(120))  #不带 Optional = NOT NULL
    type: Mapped[str] = mapped_column(String(20), default="text", index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    #关系：一个作品有多个切片；删作品时切片跟着删
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="work",
        cascade="all, delete-orphan",
    )
    #关系：作品<-->标签（经由 work_tags 中间表）
    tags: Mapped[list["Tag"]] = relationship(
        secondary=work_tags,
        back_populates="works",
    )


class Chunk(Base):
    """切片表：长文本按块切开后逐块做向量化"""

    __tablename__ = "chunks"
    #联合唯一约束：同一个作品里 chunk_index 不能重复
    __table_args__ = (UniqueConstraint("work_id", "chunk_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    #index 是 mapped_column 的参数，不是 ForeignKey 的
    work_id: Mapped[int] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"), index=True
    )
    content: Mapped[str] = mapped_column(Text)
    chunk_index: Mapped[int] = mapped_column()  #该切片在原作品中的序号，从 0 起

    work: Mapped["Work"] = relationship(back_populates="chunks")


class Tag(Base):
    """标签表"""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)  #标签名不重复

    works: Mapped[list["Work"]] = relationship(
        secondary=work_tags,
        back_populates="tags",
    )


class Conversation(Base):
    """对话表：一次完整的问答会话"""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120), default="新对话")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.id",
    )


class Message(Base):
    """消息表：对话里的每一条消息"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    #注意：写表名 conversations.id（复数），和 __tablename__ 一致
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))  #user / assistant
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
