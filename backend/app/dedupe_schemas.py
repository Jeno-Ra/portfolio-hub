"""重复素材确认的响应模型。

上传接口发现同 hash 素材时不自动做决定，而是返回 409 + 这个结构；
前端展示候选，用户选择“复用 / 仍然新建 / 取消”。
"""

from pydantic import BaseModel, Field


class DuplicateCandidate(BaseModel):
    asset_id: int
    title: str
    type: str
    projects: list[dict[str, int | str]] = Field(default_factory=list)
    content_hash: str


class DuplicateConflict(BaseModel):
    kind: str = "duplicate_asset"
    message: str
    temp_id: str
    candidates: list[DuplicateCandidate]


class DuplicateConfirm(BaseModel):
    temp_id: str
    action: str = Field(pattern="^(reuse_existing|create_new|cancel)$")
    title: str = ""
    tags: list[str] = Field(default_factory=list)
    notes: str = ""
    existing_asset_id: int | None = None
    project_id: int | None = None
    group_id: int | None = None
