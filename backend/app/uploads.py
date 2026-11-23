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
from .files import CONVERT_FIRST, SUPPORTED

# 留空则落在 backend/media（本地开发）；容器里由 compose 注入 /app/data/media（命名卷）
MEDIA_DIR = Path(settings.media_dir) if settings.media_dir else APP_DIR.parent / "media"

MAX_BYTES = 512 * 1024 * 1024  # 512 MB：手机拍的视频也够用
CHUNK = 1024 * 1024  # 1 MB 一片

# 文件名里不允许出现的字符（Windows 保留字符 + 控制符）
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

# 图片 / 视频 / 音频不走正文解析：图片交给视觉模型描述，视频和音频只存元数据
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma"}

# 上传白名单 = 能解析正文的 + 能描述/存元数据的。不在其中的（.exe / .zip / .psd…）直接拒，
# 免得用户传上来一个"能存但永远搜不到任何内容"的素材，还以为索引坏了。
ALLOWED_SUFFIXES = SUPPORTED | IMAGE_SUFFIXES | VIDEO_SUFFIXES | AUDIO_SUFFIXES

# 图片的魔数：防止把 .mkv 改名成 .png 传上来，视觉模型那边只会得到一句"解码失败"
_IMAGE_MAGIC = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".bmp": (b"BM",),
}


def safe_name(name: str) -> str:
    """只保留文件名本身，替换非法字符。"""
    base = Path(name or "").name
    base = _ILLEGAL.sub("_", base).strip(". ")
    return base[:120] or "file"


def check_suffix(name: str) -> str:
    """按扩展名做第一道校验，返回小写扩展名。不合法抛 ValueError（由路由转 400）。"""
    suffix = Path(name or "").suffix.lower()
    if suffix in CONVERT_FIRST:
        raise ValueError(f"暂不支持 {suffix}：{CONVERT_FIRST[suffix]}")
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(
            f"不支持的文件类型：{suffix or '（无扩展名）'}。"
            "可以传图片、视频、音频、PDF / Word / PPT / Excel、纯文本或代码文件"
        )
    return suffix


def check_image(name: str, path: Path) -> None:
    """图片还要看魔数：扩展名和内容对不上时早点拒，别让视觉模型去猜。"""
    suffix = Path(name or "").suffix.lower()
    magics = _IMAGE_MAGIC.get(suffix)
    if not magics:
        return
    with path.open("rb") as handle:
        head = handle.read(16)
    if suffix == ".webp":
        ok = head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    else:
        ok = any(head.startswith(magic) for magic in magics)
    if not ok:
        raise ValueError(f"{Path(name).name} 的后缀是 {suffix}，但内容不像图片（扩展名被改过？）")



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
    """判断某个路径是不是上传目录里的副本（删除素材时只清这种，绝不碰用户原文件）。"""
    try:
        path.resolve().relative_to(MEDIA_DIR.resolve())
        return True
    except (ValueError, OSError):
        return False


def discard_media(source: str | None) -> bool:
    """删掉我们自己存的副本，返回是否真的删了。

    素材可能挂在多个项目下，删素材要清副本；删项目（连素材一起删）同样要清，
    否则 media/ 会一直攒着没人引用的文件。用户自己选的文件永远不在此列。
    """
    if not source:
        return False
    path = Path(source)
    if not is_media_file(path):
        return False
    path.unlink(missing_ok=True)
    return True
