from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator


def _to_utc_iso(value: datetime) -> str:
    """统一出口格式：ISO 8601 + Z。
    数据库里是"实际上是 UTC 但没标"的 naive 对象，这里补上标记；
    万一将来拿到带偏移的 aware 对象，也一律折算回 UTC。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


# 凡是返回给客户端的时间字段都用这个类型，语义就是"绝对时刻，UTC，带 Z"
UtcDatetime = Annotated[datetime, PlainSerializer(_to_utc_iso, return_type=str)]


class WorkCreate(BaseModel):
    """新增作品时，客户端要提交的数据"""

    title: str = Field(..., min_length=1, max_length=120, description="作品标题")
    type: str = Field(
        default="text",
        description="video / image / text / code / project",
    )
    content: str = Field(default="", description="正文或描述")
    tags: list[str] = Field(default_factory=list, description="标签名，不存在会自动建")


class WorkUpdate(BaseModel):
    """部分更新：只改客户端传了的字段，没传的保持原值"""

    title: str | None = Field(default=None, min_length=1, max_length=120)
    type: str | None = None
    content: str | None = None
    tags: list[str] | None = None


class WorkResponse(WorkCreate):
    """返回给客户端的作品：多了服务端自己生成的字段"""

    # 允许直接吃 ORM 对象；不加这行，response_model 只认 dict
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: UtcDatetime

    @field_validator("tags", mode="before")
    @classmethod
    def _tags_to_names(cls, value):
        """从数据库读出来时 tags 是一串 Tag 对象，
        这里统一压成名字；客户端传进来的字符串列表原样通过。"""
        return [getattr(tag, "name", tag) for tag in value or []]


class AskRequest(BaseModel):
    """用户提问"""

    question: str = Field(..., min_length=1, max_length=500, description="用户的问题")


class AskResponse(BaseModel):
    """回答结果"""

    answer: str = Field(..., description="回答内容")
    conversation_id: int = Field(..., description="这次问答落在哪个会话里")
    sources: list[int] = Field(default_factory=list, description="答案引用的作品 id")
