"""用户设置：打包版没有 .env 文件，密钥在界面里填，存在用户自己的目录里。

三种运行方式读的是同一套逻辑：
- 开发（仓库内）：backend/设置.json，有 .env 时以 .env 为准
- 打包版：%LOCALAPPDATA%\\作品集\\设置.json
- 容器：由 compose 注入环境变量，这里不会覆盖已有值以外的东西

安全取舍：密钥是明文的。它和浏览器保存的密码一样属于「本机信任」级别——
能读到这个文件的人已经能操作你的电脑了；真正的保护边界是不把文件外传。
"""

import json
import os
from pathlib import Path

from backend.app import embedding, llm, vision
from backend.app.config import DATA_DIR, settings

CONFIG_DIR = Path(DATA_DIR) if DATA_DIR else Path(__file__).resolve().parent.parent
CONFIG_FILE = CONFIG_DIR / "设置.json"

# 界面里能改的字段，顺序即界面顺序
API_KEYS = ("deepseek_api_key", "embedding_api_key", "gpt_api_key")

LABELS = {
    "deepseek_api_key": "DeepSeek（对话）",
    "embedding_api_key": "硅基流动（向量化）",
    "gpt_api_key": "视觉模型（图片描述）",
}


def load() -> dict[str, str]:
    """读设置文件。文件不存在/损坏一律当空配置，不让它把程序卡死在启动阶段。"""
    if not CONFIG_FILE.is_file():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: str(data[key]).strip() for key in API_KEYS if str(data.get(key, "")).strip()
    }


def save(updates: dict[str, str | None]) -> None:
    """只写传进来的字段；传空字符串表示清除这一项。"""
    data = load()
    for key in API_KEYS:
        if key not in updates:
            continue
        value = str(updates[key] or "").strip()
        if value:
            data[key] = value
        else:
            data.pop(key, None)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if os.name == "posix":
        # Windows 上 chmod 基本无效（ACL 才是权限模型），POSIX 下值得做
        CONFIG_FILE.chmod(0o600)


def apply() -> None:
    """把文件里的密钥灌进 settings，并让三个模块重建 HTTP 客户端。

    必须重建：三个模块的 client 是懒加载缓存的，只改 settings 不会生效。
    """
    for key, value in load().items():
        setattr(settings, key, value)
    llm.reload()
    embedding.reload()
    vision.reload()


def masked() -> dict[str, str]:
    """只回显后 4 位：够确认「填的是哪一把」，又不至于把密钥整把发到前端。"""
    result: dict[str, str] = {}
    for key in API_KEYS:
        value = getattr(settings, key, "") or ""
        result[key] = f"****{value[-4:]}" if len(value) >= 4 else ""
    return result


def configured() -> dict[str, bool]:
    return {key: bool(getattr(settings, key, "")) for key in API_KEYS}


def storage_hint() -> str:
    """界面里显示「数据存在哪」，方便用户备份。"""
    return str(CONFIG_DIR)
