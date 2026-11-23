# ORM 数据模型：SQLAlchemy 2.x 声明式写法
#
# 两层结构：项目（Project）装素材（Asset），素材可以同时属于多个项目。
# 为什么不是"作品带一个项目字段"：一个项目里通常有视频、图片、文本、音频，
# 而这些素材的正文解析方式完全不同，必须各自成行才能分别解析、分别索引、分别引用。

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


# 多对多：素材 <--> 项目（同一份素材可以挂在多个项目下，比如两张项目共用的图）
project_assets = Table(
    "project_assets",
    Base.metadata,
    Column("project_id", ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
    Column("asset_id", ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True),
)

# 多对多：素材 <--> 标签
asset_tags = Table(
    "asset_tags",
    Base.metadata,
    Column("asset_id", ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)

# 多对多：项目 <--> 标签
project_tags = Table(
    "project_tags",
    Base.metadata,
    Column("project_id", ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Project(Base):
    """项目：一个作品的"总集"。标题、简介、封面，下面挂任意多个素材。"""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    summary: Mapped[str] = mapped_column(Text, default="")  # 简介/创作说明
    cover_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    assets: Mapped[list["Asset"]] = relationship(
        secondary=project_assets,
        back_populates="projects",
        order_by="Asset.id",
    )
    tags: Mapped[list["Tag"]] = relationship(
        secondary=project_tags,
        back_populates="projects",
    )


class Asset(Base):
    """素材：图片 / 视频 / 音频 / 文本 / 代码 / 文档中的一条。

    type 是内置分类（见 kinds.py），不是用户自定义标签；索引、解析、筛选都按它走。
    """

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(20), default="", index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    # 原始文件路径（上传副本或服务器上的原文件），用于溯源
    source_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
    )
    projects: Mapped[list["Project"]] = relationship(
        secondary=project_assets,
        back_populates="assets",
    )
    tags: Mapped[list["Tag"]] = relationship(
        secondary=asset_tags,
        back_populates="assets",
    )


class Chunk(Base):
    """切片表：素材正文按块切开后逐块做向量化"""

    __tablename__ = "chunks"
    __table_args__ = (UniqueConstraint("asset_id", "chunk_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), index=True
    )
    content: Mapped[str] = mapped_column(Text)
    chunk_index: Mapped[int] = mapped_column()  # 该切片在原素材中的序号，从 0 起

    asset: Mapped["Asset"] = relationship(back_populates="chunks")


class Tag(Base):
    """标签表：项目与素材共用一张表，靠中间表区分挂在哪边"""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)

    assets: Mapped[list["Asset"]] = relationship(
        secondary=asset_tags,
        back_populates="tags",
    )
    projects: Mapped[list["Project"]] = relationship(
        secondary=project_tags,
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
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))  # user / assistant
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
