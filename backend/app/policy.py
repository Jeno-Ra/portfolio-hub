"""权限策略：AI 能改什么、不能改什么。

为什么要有这个文件：
意图路由让 AI 能执行"改标题""删作品"这类写操作，一旦放开了「改内容」，
它就可能覆盖用户辛苦录入的作品正文——而正文往往来自原始文件（图片描述、PDF 正文、
扫描件 OCR 结果），改掉等于丢数据。所以边界必须写在代码里，而不是写在提示词里。

四级边界：

1. **可改（系统元数据）**：标题 title、标签 tags、类型 type
   —— 这些是"系统的索引信息"，改了不丢真实内容，误改也容易恢复。

2. **不可改（作品内容）**：content
   —— 内容来自用户原始文件或用户自己撰写，AI 不得通过对话覆盖。
   要改只能由用户在前端点「编辑」，或重新录入文件。

3. **不碰（磁盘文件）**：source_path 指向的原始文件
   —— 本项目的后端代码**只读不写**：所有读取都用 read_bytes，
   全程没有任何 write_text / open(..., "w") / unlink 调用。
   AI 即使想改也改不到，因为它拿到的只是路径字符串，没有任何写文件的能力。

4. **可删但要确认**：删除作品
   —— 只删数据库记录与向量，**不删磁盘上的原文件**；且必须二次确认。
"""

from typing import Final

# AI 可以通过对话修改的字段
AI_EDITABLE_FIELDS: Final[set[str]] = {"title", "tags", "type"}

# 明确禁止 AI 修改的字段 → 中文说明（用于给用户解释为什么拒绝）
AI_READONLY_FIELDS: Final[dict[str, str]] = {
    "content": "作品内容",
    "source_path": "原始文件路径",
}

FIELD_LABEL: Final[dict[str, str]] = {
    "title": "标题",
    "tags": "标签",
    "type": "类型",
    "content": "内容",
    "source_path": "原始文件路径",
}


def check_edit_field(field: str) -> tuple[bool, str]:
    """判断某个字段能不能由 AI 修改，返回 (是否允许, 拒绝理由)。"""
    if field in AI_EDITABLE_FIELDS:
        return True, ""

    if field in AI_READONLY_FIELDS:
        label = FIELD_LABEL.get(field, field)
        return False, (
            f"「{label}」不允许通过对话修改：它来自你的原始文件或你亲手写的内容，"
            f"改掉就等于丢数据。要改请在前端点「编辑」手动修改，或者重新录入文件。"
            f"我可以帮你改标题、标签、类型这些索引信息。"
        )

    return False, f"不支持修改字段「{field}」。我可以帮你改标题、标签、类型。"
