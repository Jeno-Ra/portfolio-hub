"""接收浏览器上传的文件并落盘。

为什么需要这一层：
    浏览器出于安全不能把本地文件的绝对路径告诉页面（`<input type=file>` 只给内容，
    不给 `C:\\...`），所以"按路径录入"在网页里根本没法用——只能把文件本体传上来。

落盘策略：
    - 文件名前加 8 位随机前缀，同名文件不会互相覆盖
    - 只取 basename，防止 `../../etc/passwd` 这类路径穿越
    - 边读边写，超过上限立刻中止并删掉半截文件，不把整个文件读进内存
"""

import re
import uuid
from pathlib import Path

from fastapi import UploadFile

from .config import APP_DIR, settings

# 留空则落在 backend/media（本地开发）；容器里由 compose 注入 /app/data/media（命名卷）
MEDIA_DIR = Path(settings.media_dir) if settings.media_dir else APP_DIR.parent / "media"

MAX_BYTES = 512 * 1024 * 1024  # 512 MB：手机拍的视频也够用
CHUNK = 1024 * 1024  # 1 MB 一片

# 文件名里不允许出现的字符（Windows 保留字符 + 控制符）
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(name: str) -> str:
    """只保留文件名本身，替换非法字符。"""
    base = Path(name or "").name
    base = _ILLEGAL.sub("_", base).strip(". ")
    return base[:120] or "file"


def save_upload(upload: UploadFile) -> Path:
    """把上传流写进 MEDIA_DIR，返回落盘路径。失败时抛 ValueError。"""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    target = MEDIA_DIR / f"{uuid.uuid4().hex[:8]}_{safe_name(upload.filename or '')}"

    total = 0
    try:
        with target.open("wb") as out:
            while True:
                chunk = upload.file.read(CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError(
                        f"文件超过 {MAX_BYTES // (1024 * 1024)} MB 上限，请先压缩或裁剪"
                    )
                out.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)  # 别留半截文件
        raise

    if total == 0:
        target.unlink(missing_ok=True)
        raise ValueError("文件是空的")

    return target


def is_media_file(path: Path) -> bool:
    """判断某个路径是不是上传目录里的副本（删除作品时只清这种，绝不碰用户原文件）。"""
    try:
        path.resolve().relative_to(MEDIA_DIR.resolve())
        return True
    except (ValueError, OSError):
        return False
