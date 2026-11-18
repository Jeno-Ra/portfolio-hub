"""文件解析：把各种文档读成纯文本，供切片与索引。

分工：
- vision.py 处理"看得见但读不出字"的东西（图片）
- files.py 处理"本来就是文字"的东西（PDF / docx / pptx / xlsx / 文本 / 代码）

支持与不支持的边界（都在 SUPPORTED 里显式写明）：
- ✅ pdf、docx、pptx、xlsx/xlsm、rtf、html、ipynb、srt/vtt、纯文本与代码
- ❌ doc（Word 97-2003 二进制）、odt/ods/odp、pages、epub —— 需要先转成新格式
- ❌ 视频抽帧、语音转写（规划里明确砍掉，成本约 40 小时）

注意：docx / pptx / xlsx 里的**表格必须单独读**，只取段落会静默丢掉全部表格数据。
"""

import json
import re
from html import unescape
from pathlib import Path

from docx import Document
from openpyxl import load_workbook
from pptx import Presentation
from pypdf import PdfReader
from striprtf.striprtf import rtf_to_text

# 直接按文本读的扩展名
TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".json",
    ".yaml",
    ".yml",
    ".sql",
    ".log",
    ".ini",
    ".toml",
    ".xml",
}

# 代码类扩展名（和 TEXT_SUFFIXES 一样按 utf-8/gbk 读，分开只是为了语义清晰）
CODE_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".cpp",
    ".c",
    ".h",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".css",
    ".scss",
    ".sh",
    ".ps1",
}

SUPPORTED = (
    TEXT_SUFFIXES
    | CODE_SUFFIXES
    | {
        ".pdf",
        ".docx",
        ".pptx",
        ".xlsx",
        ".xlsm",
        ".rtf",
        ".html",
        ".htm",
        ".ipynb",
        ".srt",
        ".vtt",
    }
)

# 需要先转换才能读的格式，给出可操作的提示而不是一句"不支持"
CONVERT_FIRST = {
    ".doc": "Word 97-2003 老格式，请用 Word 另存为 .docx",
    ".odt": "OpenDocument 格式，请另存为 .docx",
    ".ods": "OpenDocument 表格，请另存为 .xlsx",
    ".odp": "OpenDocument 演示，请另存为 .pptx",
    ".pages": "Apple Pages 格式，请导出为 .docx 或 .pdf",
    ".epub": "电子书格式，请先用工具转成 .txt 或 .pdf",
    ".ppt": "PowerPoint 97-2003 老格式，请另存为 .pptx",
    ".xls": "Excel 97-2003 老格式，请另存为 .xlsx",
}


def extract_text(path: str | Path) -> str:
    """按扩展名解析文件，返回纯文本。不支持的类型抛 ValueError。"""
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"文件不存在：{file_path}")

    suffix = file_path.suffix.lower()
    text = _dispatch(file_path, suffix)

    text = text.strip()
    if not text:
        raise ValueError(f"这个文件提取不到文字：{file_path.name}")
    return text


def _dispatch(file_path: Path, suffix: str) -> str:
    if suffix in TEXT_SUFFIXES or suffix in CODE_SUFFIXES:
        return _read_text(file_path)
    if suffix == ".pdf":
        return _read_pdf(file_path)
    if suffix == ".docx":
        return _read_docx(file_path)
    if suffix == ".pptx":
        return _read_pptx(file_path)
    if suffix in {".xlsx", ".xlsm"}:
        return _read_xlsx(file_path)
    if suffix == ".rtf":
        return _read_rtf(file_path)
    if suffix in {".html", ".htm"}:
        return _read_html(file_path)
    if suffix == ".ipynb":
        return _read_ipynb(file_path)
    if suffix in {".srt", ".vtt"}:
        return _read_subtitle(file_path)

    if suffix in CONVERT_FIRST:
        raise ValueError(f"暂不支持 {suffix}：{CONVERT_FIRST[suffix]}")
    raise ValueError(
        f"不支持的文件类型：{suffix or '（无扩展名）'}。"
        f"当前支持：{', '.join(sorted(SUPPORTED))}"
    )


# ---------- 各类格式的具体实现 ----------


def _read_text(file_path: Path) -> str:
    raw = file_path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法解码文本文件：{file_path.name}")


def _read_pdf(file_path: Path) -> str:
    reader = PdfReader(str(file_path))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    text = "\n\n".join(p for p in pages if p)
    if not text:
        raise ValueError("这个 PDF 提取不到文字，可能是扫描件（图片型 PDF）")
    return text


def _read_docx(file_path: Path) -> str:
    doc = Document(str(file_path))
    parts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:  # 表格必须单独读，否则整表丢失
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _read_pptx(file_path: Path) -> str:
    prs = Presentation(str(file_path))
    parts: list[str] = []
    for index, slide in enumerate(prs.slides, start=1):
        parts.append(f"【第 {index} 页】")
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    line = "".join(run.text for run in para.runs).strip()
                    if line:
                        parts.append(line)
            if shape.has_table:  # 幻灯片里的表格同样要单独取
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells):
                        parts.append(" | ".join(cells))
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parts.append(f"备注：{notes}")
    return "\n".join(parts)


def _read_xlsx(file_path: Path) -> str:
    wb = load_workbook(filename=str(file_path), read_only=True, data_only=True)
    parts: list[str] = []
    try:
        for sheet in wb.worksheets:
            rows: list[str] = []
            for row in sheet.iter_rows(values_only=True):
                cells = ["" if v is None else str(v) for v in row]
                if any(c.strip() for c in cells):
                    rows.append(" | ".join(cells).rstrip(" |"))
            if rows:
                parts.append(f"【工作表：{sheet.title}】\n" + "\n".join(rows))
    finally:
        wb.close()
    return "\n\n".join(parts)


def _read_rtf(file_path: Path) -> str:
    return rtf_to_text(_read_text(file_path))


def _read_html(file_path: Path) -> str:
    html = _read_text(file_path)
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = unescape(text)
    return re.sub(r"[ \t]{2,}", " ", text)


def _read_ipynb(file_path: Path) -> str:
    nb = json.loads(_read_text(file_path))
    parts: list[str] = []
    for cell in nb.get("cells", []):
        source = "".join(cell.get("source", []))
        if not source.strip():
            continue
        if cell.get("cell_type") == "code":
            parts.append(f"```python\n{source.strip()}\n```")
        else:
            parts.append(source.strip())
    return "\n\n".join(parts)


def _read_subtitle(file_path: Path) -> str:
    lines = []
    for line in _read_text(file_path).splitlines():
        stripped = line.strip()
        if not stripped or stripped.isdigit():
            continue
        if "-->" in stripped:  # 时间轴行
            continue
        if stripped.upper().startswith(("WEBVTT", "NOTE")):
            continue
        lines.append(stripped)
    # 相邻重复行去掉（字幕常见）
    deduped = [line for i, line in enumerate(lines) if i == 0 or line != lines[i - 1]]
    return "\n".join(deduped)
