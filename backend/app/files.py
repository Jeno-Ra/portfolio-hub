"""文件解析：把 PDF / 文本 / 代码文件读成字符串，供切片与索引。

与 vision.py 的分工：
- vision.py 处理"看得见但读不出字"的东西（图片）
- files.py 处理"本来就是文字"的东西（PDF 正文、md、代码）

不做的：视频抽帧、语音转写（规划里明确砍掉，成本约 40 小时）。
"""

from pathlib import Path

from pypdf import PdfReader

# 纯文本类扩展名：直接按文本读
TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".py",
    ".js",
    ".ts",
    ".html",
    ".css",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".sql",
    ".java",
    ".cpp",
    ".c",
    ".go",
    ".rs",
}


def extract_text(path: str | Path) -> str:
    """按扩展名解析文件，返回纯文本。不支持的类型抛 ValueError。"""
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"文件不存在：{file_path}")

    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        reader = PdfReader(str(file_path))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
        text = "\n\n".join(p for p in pages if p)
        if not text:
            raise ValueError("这个 PDF 提取不到文字，可能是扫描件（图片型 PDF）")
        return text

    if suffix in TEXT_SUFFIXES:
        raw = file_path.read_bytes()
        for encoding in ("utf-8", "utf-8-sig", "gbk"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ValueError(f"无法解码文本文件（尝试过 utf-8 / gbk）：{file_path.name}")

    raise ValueError(f"不支持的文件类型：{suffix or '（无扩展名）'}")
