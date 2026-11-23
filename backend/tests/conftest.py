"""接口测试的公共装置。

两条硬要求：
1. **不联网**：embedding、对话、视觉、向量库全部换成假实现（见 fakes.py）。
2. **不碰真实数据**：库文件、上传目录都指到临时目录，跑完即弃；每个用例前重建表。
"""

import os
import shutil
import tempfile
from pathlib import Path

# config 是模块级单例（导入时读一次环境变量），所以这几行必须排在任何 backend.app 导入之前
_TMP = Path(tempfile.mkdtemp(prefix="portfolio-hub-tests-"))
os.environ["DB_PATH"] = str(_TMP / "test.db")
os.environ["MEDIA_DIR"] = str(_TMP / "media")
os.environ["CHROMA_DIR"] = str(_TMP / "chroma")

# 三个 key 在 config.py 里是必填项。测试全程不联网，给占位值就够——
# 这样在没有 .env 的机器上（CI、别人的电脑）也能直接跑 pytest。
os.environ["DEEPSEEK_API_KEY"] = "test-key"
os.environ["EMBEDDING_API_KEY"] = "test-key"
os.environ["GPT_API_KEY"] = "test-key"

MEDIA = _TMP / "media"

import pytest

from backend.app import llm, main, vector_store
from backend.app.database import Base, engine
from backend.tests import fakes


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """把一切外部依赖换成本地假实现，并保证每个用例从空库开始。"""
    fakes.reset()

    monkeypatch.setattr(main, "index_work", lambda *a, **k: None)
    monkeypatch.setattr(main, "embed_text", fakes.fake_embed)
    monkeypatch.setattr(llm, "chat", fakes.fake_chat)
    monkeypatch.setattr(llm, "chat_stream", fakes.fake_chat_stream)
    monkeypatch.setattr(llm, "raw_chat", fakes.fake_raw_chat)
    monkeypatch.setattr(vector_store, "query", fakes.fake_query)
    monkeypatch.setattr(vector_store, "delete_work", lambda *a, **k: None)

    shutil.rmtree(MEDIA, ignore_errors=True)
    MEDIA.mkdir(parents=True, exist_ok=True)

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    yield

    shutil.rmtree(MEDIA, ignore_errors=True)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def sample_work(client):
    """一条现成的作品，多数用例从这里起步。"""
    response = client.post(
        "/works",
        json={
            "title": "长城拍摄笔记",
            "type": "text",
            "content": "金山岭段 19 个镜头，航拍 6 个，重点拍敌楼晨雾。",
            "tags": ["拍摄", "长城"],
        },
    )
    assert response.status_code == 201
    return response.json()
