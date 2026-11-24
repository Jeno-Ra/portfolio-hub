from datetime import UTC, datetime
from typing import Annotated

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
)

from backend.app.kinds import LABELS


def _to_utc_iso(value: datetime) -> str:
    """统一出口格式：ISO 8601 + Z。
    数据库里是"实际上是 UTC 但没标"的 naive 对象，这里补上标记；
    万一将来拿到带偏移的 aware 对象，也一律折算回 UTC。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


# 凡是返回给客户端的时间字段都用这个类型，语义就是"绝对时刻，UTC，带 Z"
UtcDatetime = Annotated[datetime, PlainSerializer(_to_utc_iso, return_type=str)]


def _clean_kind(value) -> str:
    """素材类型由代码判定（见 kinds.py），客户端传了也要对得上，防止出现解析不了的值。

    留空是允许的：录入时如果判定不出来，就先存成空，界面上显示「未分类」。
    """
    cleaned = (value or "").strip().lower()
    if cleaned and cleaned not in LABELS:
        allowed = " / ".join(LABELS.values())
        raise ValueError(f"类型只能是 {allowed}，或者留空表示未分类")
    return cleaned


def _tags_to_names(value):
    """ORM 读出来是 Tag 对象列表，客户端传进来是字符串列表，统一压成名字。"""
    return [getattr(tag, "name", tag) for tag in value or []]


class AssetCreate(BaseModel):
    """新增素材（手写内容，或上传时由服务端填）"""

    title: str = Field(..., min_length=1, max_length=120, description="素材标题")
    type: str = Field(
        default="", description="image / video / audio / text / code / document；留空 = 未分类"
    )
    content: str = Field(default="", description="正文或描述")
    tags: list[str] = Field(default_factory=list, description="标签名，不存在会自动建")
    project_ids: list[int] = Field(
        default_factory=list, description="要挂到哪些项目下（可多个，素材可复用）"
    )
    group_id: int | None = Field(default=None, description="所属分组；仅当 project_ids 单项目时生效")

    @field_validator("type", mode="before")
    @classmethod
    def _validate_type(cls, value):
        return _clean_kind(value)


class AssetUpdate(BaseModel):
    """部分更新：只改客户端传了的字段，没传的保持原值"""

    title: str | None = Field(default=None, min_length=1, max_length=120)
    type: str | None = None
    content: str | None = None
    tags: list[str] | None = None
    project_ids: list[int] | None = Field(
        default=None, description="传了就整体替换该素材所属的项目集合"
    )

    @field_validator("type", mode="before")
    @classmethod
    def _validate_type(cls, value):
        # 不传 = 不改；传空串 = 清成未分类；传别的必须合法
        return None if value is None else _clean_kind(value)


class AssetResponse(BaseModel):
    """返回给客户端的素材"""

    model_config = ConfigDict(from_attributes=True)  # 允许直接吃 ORM 对象

    id: int
    title: str
    type: str = ""
    content: str = ""
    source_path: str | None = None
    created_at: UtcDatetime
    tags: list[str] = Field(default_factory=list)
    # ORM 上的属性叫 projects（对象列表），客户端传进来叫 project_ids（id 列表），
    # 两个名字都认，统一压成 id
    project_ids: list[int] = Field(
        default_factory=list,
        validation_alias=AliasChoices("project_ids", "projects"),
    )
    project_group_id: int | None = None

    @field_validator("tags", mode="before")
    @classmethod
    def _tags(cls, value):
        return _tags_to_names(value)

    @field_validator("project_ids", mode="before")
    @classmethod
    def _projects(cls, value):
        return [getattr(item, "id", item) for item in value or []]


class ProjectCreate(BaseModel):
    """新增项目：项目是容器，本身没有"类型"，只有标题/简介/封面/标签"""

    title: str = Field(..., min_length=1, max_length=120, description="项目名")
    summary: str = Field(default="", description="一句话简介 / 创作说明")
    tags: list[str] = Field(default_factory=list)


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    summary: str | None = None
    tags: list[str] | None = None


class ProjectResponse(BaseModel):
    """项目列表里的一行：带素材件数与类型分布，列表上就能看出这个项目是否多元"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    summary: str = ""
    cover_path: str | None = None
    cover_url: str | None = Field(
        default=None, description="封面在浏览器里能直接用的地址（/media/...）"
    )
    created_at: UtcDatetime
    updated_at: UtcDatetime | None = None
    tags: list[str] = Field(default_factory=list)
    asset_count: int = 0
    kind_counts: dict[str, int] = Field(default_factory=dict)

    @field_validator("tags", mode="before")
    @classmethod
    def _tags(cls, value):
        return _tags_to_names(value)


class ProjectDetail(ProjectResponse):
    """项目详情：项目本身 + 它下面的全部素材"""

    assets: list[AssetResponse] = Field(default_factory=list)


class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    parent_id: int | None = None

class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    parent_id: int | None = None

class GroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    name: str
    parent_id: int | None = None

class AssetGroupMove(BaseModel):
    group_id: int | None = None


class AssetGroupBatchMove(BaseModel):
    asset_ids: list[int] = Field(..., min_length=1, description="要移动的素材 id")
    group_id: int | None = None


class LinkAssetsRequest(BaseModel):
    """把已有素材挂到项目下（已有素材复用，不是新建）"""

    asset_ids: list[int] = Field(..., min_length=1, description="要挂上的素材 id")


class IngestRequest(BaseModel):
    """从本地文件录入素材：图片 / 视频 / 音频 / PDF / 文本 / 代码 都能走这一个入口。

    - 图片：调视觉模型生成描述，写进 content
    - 视频 / 音频：只存元数据（不做抽帧 / 语音转写），描述用手填的 notes；
      视频给 cover_path 时会额外描述封面画面并拼进 content
    - PDF/文本/代码：解析正文写进 content

    解析方式由文件扩展名决定（见 kinds.py），不看客户端传的 type。
    """

    path: str = Field(..., min_length=1, description="文件绝对路径")
    title: str | None = Field(default=None, max_length=120, description="留空则用文件名")
    type: str | None = Field(
        default=None, description="留空则按扩展名判定；填了必须合法"
    )
    tags: list[str] = Field(default_factory=list)
    notes: str = Field(default="", description="手填描述（视频/音频必填，其他可选）")
    cover_path: str | None = Field(default=None, description="仅视频：封面图路径")
    project_ids: list[int] = Field(default_factory=list, description="要挂到哪些项目下")


class AskRequest(BaseModel):
    """用户提问"""

    question: str = Field(..., min_length=1, max_length=500, description="用户的问题")
    confirm: bool = Field(
        default=False,
        description="删除类操作必须显式确认；首次请求只返回预览",
    )


class SourceRef(BaseModel):
    """答案引用到的素材：编号、id、标题、所属项目。

    界面直接拿这四项渲染「[1] 《项目 · 素材》」，不用再按 id 反查一次。
    """

    index: int = Field(..., description="回答里的引用编号，从 1 开始")
    asset_id: int
    title: str
    project: str = Field(default="", description="素材所属项目，可能为空")


class AskResponse(BaseModel):
    """回答结果"""

    answer: str = Field(..., description="回答内容")
    conversation_id: int = Field(..., description="这次问答落在哪个会话里")
    sources: list[SourceRef] = Field(
        default_factory=list, description="答案引用的素材，顺序与正文里的 [1] [2] 一致"
    )


class SettingsUpdate(BaseModel):
    """界面里填的密钥。None = 这一项不动，"" = 清除，其他 = 覆盖。

    用 None 而不是默认空串，是为了区分"没提交这一项"和"提交了空值要清掉"。
    """

    deepseek_api_key: str | None = None
    embedding_api_key: str | None = None
    gpt_api_key: str | None = None
