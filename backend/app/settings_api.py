"""设置接口：打包版没有 .env，密钥由用户在界面里填，存到用户目录的 设置.json。

单独成模块的原因：这块全是"给人用"的逻辑（掩码回显、探活、重建索引、退出程序），
跟作品、问答两条业务线没关系，混在 main.py 里只会让那个文件继续变胖。
"""

import os
import threading

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app import embedding, errors, indexer, llm, user_config, vision
from backend.app.config import IS_FROZEN, settings
from backend.app.database import get_db
from backend.app.models import Asset
from backend.app.response import Envelope, ok
from backend.app.schemas import SettingsUpdate

router = APIRouter()


def _settings_payload() -> dict:
    return {
        "configured": user_config.configured(),
        "keys": user_config.masked(),
        "labels": user_config.LABELS,
        "storage": user_config.storage_hint(),
        # 打包版才给「退出程序」按钮：容器/开发环境里退出没有意义
        "portable": IS_FROZEN,
    }


def _reload_clients() -> None:
    llm.reload()
    embedding.reload()
    vision.reload()


def _reindex_all(db: Session) -> int:
    """把库里所有素材重建索引，返回成功的条数。

    没配向量化密钥时直接返回 0，不报错——用户可能只填了对话密钥。
    """
    if not settings.embedding_api_key:
        return 0

    done = 0
    for asset in db.query(Asset).all():
        try:
            indexer.index_asset(db, asset)
            done += 1
        except (errors.NotConfigured, RuntimeError):
            break  # 密钥/网络有问题就停，别对着每条素材重复失败
    return done


def _probe(key: str, label: str, probe, value: str | None) -> dict:
    """探活一个服务，返回 {service, ok, message}。

    `value is None` 表示"用已保存的密钥测"；给了值就临时灌进去测完再还原，
    避免把没验证过的密钥留在内存里。
    """
    original = getattr(settings, key)
    if value is None and not original:
        return {"service": label, "ok": False, "message": "还没有填"}

    if value is not None:
        setattr(settings, key, value.strip())
        _reload_clients()

    try:
        probe()
        return {"service": label, "ok": True, "message": "连接正常"}
    except errors.NotConfigured:
        return {"service": label, "ok": False, "message": "没有填密钥"}
    except Exception as exc:  # noqa: BLE001 — 探活要如实回报任何失败原因
        return {"service": label, "ok": False, "message": str(exc)[:200]}
    finally:
        if value is not None:
            setattr(settings, key, original)
            _reload_clients()


@router.get("/settings", response_model=Envelope[dict])
def read_settings():
    return ok(_settings_payload())


@router.put("/settings", response_model=Envelope[dict])
def update_settings(payload: SettingsUpdate, db: Session = Depends(get_db)):
    """保存密钥 → 立刻生效 → 顺手把之前没索引成功的素材补上。

    补索引放在这里而不是后台任务：素材通常只有几条，同步做完再返回，
    用户看到「已为 N 件素材建好索引」比等一个看不见的后台任务更安心。
    """
    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="没有要保存的字段")

    user_config.save(updates)
    user_config.apply()

    indexed = _reindex_all(db)
    message = f"已保存，为 {indexed} 件素材重建了索引"
    return ok({**_settings_payload(), "indexed_works": indexed}, message)


@router.post("/settings/test", response_model=Envelope[dict])
def test_settings(payload: SettingsUpdate):
    """逐个服务探活：用户填错哪把钥匙，一眼看得出来。

    三个探测都是「不消耗生成额度」的调用（列模型 / 一次极短向量化），
    所以点几次也不心疼。
    """
    probes = {
        "deepseek_api_key": ("DeepSeek（对话）", llm.ping),
        "embedding_api_key": ("硅基流动（向量化）", embedding.ping),
        "gpt_api_key": ("视觉模型（图片描述）", vision.ping),
    }
    provided = payload.model_dump(exclude_none=True)
    results = [
        _probe(key, label, probe, provided.get(key))
        for key, (label, probe) in probes.items()
    ]

    failed = [item["service"] for item in results if not item["ok"]]
    message = "全部连接正常" if not failed else f"{'、'.join(failed)} 没通过"
    return ok({"results": results}, message)


@router.post("/quit", response_model=Envelope[dict])
def quit_app():
    """打包版专用：关窗口不会结束进程，需要一个显式的退出口。

    延迟 0.3 秒再退出，是为了先把 HTTP 200 发回去——否则前端只会看到「连接被重置」。
    """
    if not IS_FROZEN:
        raise HTTPException(status_code=400, detail="只有打包版支持退出")

    threading.Timer(0.3, lambda: os._exit(0)).start()
    return ok({"exit": True}, "正在退出")
