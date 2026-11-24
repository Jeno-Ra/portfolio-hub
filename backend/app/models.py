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
#
# 为什么用中间表而不是在 assets 里放 project_id？
# 因为同一份素材可以复用到多个项目；如果只放一个 project_id，复用就只能复制文件。
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

class AssetGroup(Base):
    """项目内的分组树节点。

    这是自引用表：parent_id 指向同一张表的另一个 id，因此可以表达任意深度：

        角色素材（parent_id=NULL）
        └── 蒙恬（parent_id=角色素材.id）

    同一个项目、同一个父分组下不能出现同名节点；但不同父分组可以各有一个同名子组。
    """

    __tablename__ = "asset_groups"
    __table_args__ = (UniqueConstraint("project_id", "parent_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("asset_groups.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    project: Mapped["Project"] = relationship(back_populates="groups")
    parent: Mapped["AssetGroup | None"] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list["AssetGroup"]] = relationship(back_populates="parent")

# 项目内的“素材分组关系”。
# project_assets 只回答“素材属于哪些项目”；这个表再回答“它在该项目哪个组”。
# group_id 允许 NULL：NULL 就是“待归类”，比硬塞进一个“根目录”虚拟组更简单。
# (project_id, asset_id) 唯一：一件素材在同一个项目里只能有一个归属组。
project_asset_groups = Table(
    "project_asset_groups", Base.metadata,
    Column("project_id", ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
    Column("asset_id", ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True),
    Column("group_id", ForeignKey("asset_groups.id", ondelete="CASCADE"), nullable=True),
    UniqueConstraint("project_id", "asset_id"),
)


class Project(Base):
    """项目：一个作品的"总集"。

    项目只负责组织，不负责保存具体文件内容：
    - title/summary/cover_path 是项目自己的信息
    - assets 是多对多素材关系
    - groups 是当前项目自己的分组树

    因此删除项目默认只删除这个容器，不应顺手删掉素材；
    同一个素材可能还被其他项目引用。
    """

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
    # 删除项目时级联删除项目自己的分组和关系记录；不会因为这个关系自动删除 Asset。
    groups: Mapped[list["AssetGroup"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    tags: Mapped[list["Tag"]] = relationship(
        secondary=project_tags,
        back_populates="projects",
    )


class Asset(Base):
    """素材：图片 / 视频 / 音频 / 文本 / 代码 / 文档中的一条。

    Asset 是系统里真正的内容实体：项目只是引用它。
    同一个 Asset 可以挂到多个 Project，这也是项目/素材拆开建模的核心原因。

    type 是内置分类（见 kinds.py），不是用户自定义标签；索引、解析、筛选都按它走。
    content 是供检索的正文/描述，source_path 只是溯源地址，不代表 AI 有权修改那个原文件。
    """

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(20), default="", index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    # 原始文件路径（上传副本或服务器上的原文件），用于溯源
    source_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 文件内容 SHA-256：用于上传重复检测；手写/路径录入可以为空。
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
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
    """素材正文的一个检索片段。

    原文 500 字一块、相邻块重叠 50 字；同一个素材的 chunk_index 从 0 开始递增。
    PostgreSQL 保存它，Chroma 保存同一片段的向量；两边用 asset_id + chunk_index 对齐。
    """

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
    """一组问答的容器。

    当前问答检索主要依赖当前请求与 LAST_HITS；Conversation/Message 负责记录历史，
    后续如果要支持多轮指代，可以把上下文或 last_sources 挂到这里。
    """

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
