"""内置分类：素材的 6 种固定类型。

为什么是内置而不是让用户自定义：
    类型决定「正文怎么来」和「界面上怎么分组」。图片要走视觉模型描述、
    视频音频只能记元数据、文档代码抽文字——这条链路必须由代码认识这几个值，
    用户随便起名（"照片""影像"）会让解析逻辑无从判断。

和「项目」的关系：
    项目是容器，不是类型。一个项目里同时有视频、图片、文本、音频很正常，
    所以 `type` 是素材的属性，项目本身没有类型（见 models.py）。
"""

from pathlib import Path

from backend.app.files import CODE_SUFFIXES
from backend.app.uploads import AUDIO_SUFFIXES, IMAGE_SUFFIXES, VIDEO_SUFFIXES

IMAGE = "image"
VIDEO = "video"
AUDIO = "audio"
TEXT = "text"
CODE = "code"
DOCUMENT = "document"

# 固定顺序 = 界面上筛选按钮的顺序（图片在最前，作品集里最常见）
LABELS: dict[str, str] = {
    IMAGE: "图片",
    VIDEO: "视频",
    AUDIO: "音频",
    TEXT: "文本",
    CODE: "代码",
    DOCUMENT: "文档",
}

# 需要"抽正文"的文档格式；其余能读的（md/txt/srt/vtt）归到文本
DOCUMENT_SUFFIXES = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".xlsm",
    ".rtf",
    ".html",
    ".ipynb",
}


def label(kind: str) -> str:
    """类型码翻中文，未知值原样返回（历史数据不至于显示成空白）。"""
    return LABELS.get(kind, kind or "未分类")


def of(path: Path) -> str:
    """按扩展名判定内置类型。

    这是“类型”和“标签”的区别：标签只是搜索/筛选元数据；
    类型会决定后续正文怎么生成，例如 image 走视觉模型、document 走解析器。
    传进来的路径可能已经是上传后的随机文件名，但后缀仍保留，所以判断仍可靠。
    """
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return IMAGE
    if suffix in VIDEO_SUFFIXES:
        return VIDEO
    if suffix in AUDIO_SUFFIXES:
        return AUDIO
    if suffix in CODE_SUFFIXES:
        return CODE
    if suffix in DOCUMENT_SUFFIXES:
        return DOCUMENT
    return TEXT
