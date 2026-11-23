"""接口层测试：用 TestClient 直接打路由，不启服务器、不联网。

覆盖七类行为：
1. 统一返回格式（成功信封、错误信封、请求号）
2. 作品增删改查 + 404
3. AI 只能改索引信息（标题），改内容被策略拦下
4. 删除必须二次确认
5. 网页上传：落盘 → 解析入库 → 删除时清副本；扩展名/魔数校验
6. 检索没命中不调模型；命中时把目录与片段喂给模型；流式事件顺序
7. 缺密钥时给可读的 400（而不是 500）
"""

import re
from pathlib import Path

from backend.tests.conftest import body
from backend.tests.fakes import CALLS, FAKE_HITS


def test_runs_on_temporary_database():
    """保护性断言：万一环境变量没生效，测试就会写进真实库——必须先拦住。"""
    from backend.app.database import DB_PATH

    assert "portfolio-hub-tests-" in str(DB_PATH)


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200

    payload = response.json()
    assert payload["code"] == 0
    assert payload["data"]["status"] == "ok"
    assert payload["data"]["version"]  # 带版本号，排查"跑的是哪个版本"用


def test_envelope_and_request_id(client):
    """成功响应的形状 + 请求号：前端只认 code，出问题靠请求号对日志。"""
    response = client.get("/works")
    payload = response.json()

    assert set(payload) == {"code", "message", "data"}
    assert payload["code"] == 0
    assert payload["message"] == "共 0 条"  # message 是给人看的，不是固定 "ok"

    request_id = response.headers["X-Request-ID"]
    assert re.fullmatch(r"[0-9a-f]{8}", request_id)


def test_error_envelopes(client):
    """错误也长成信封：404 是 1003，422 是 1005 且能指出哪个字段。"""
    missing = client.get("/works/9999")
    assert missing.status_code == 404
    assert missing.json()["code"] == 1003
    assert missing.json()["data"] is None

    invalid = client.post("/ask", json={})
    assert invalid.status_code == 422
    payload = invalid.json()
    assert payload["code"] == 1005
    assert "参数错误" in payload["message"]
    assert "question" in payload["message"]  # 说清楚是哪个字段缺了


def test_work_crud(client, sample_work):
    work_id = sample_work["id"]
    assert sample_work["title"] == "长城拍摄笔记"
    assert {tag for tag in sample_work["tags"]} == {"拍摄", "长城"}
    assert sample_work["source_path"] is None  # 手写的作品没有源文件

    assert [w["id"] for w in body(client.get("/works"))] == [work_id]

    assert body(client.get(f"/works/{work_id}"))["title"] == "长城拍摄笔记"

    renamed = client.patch(f"/works/{work_id}", json={"title": "金山岭拍摄笔记"})
    assert renamed.status_code == 200
    assert body(renamed)["title"] == "金山岭拍摄笔记"
    assert client.patch("/works/9999", json={"title": "x"}).status_code == 404

    # 删除走 200 + 信封（204 不能带响应体，前端就没法统一按 code 判断）
    deleted = client.delete(f"/works/{work_id}")
    assert deleted.status_code == 200
    assert body(deleted) == {"id": work_id}
    assert client.get(f"/works/{work_id}").status_code == 404
    assert client.delete(f"/works/{work_id}").status_code == 404  # 删两次，第二次 404


def test_ai_can_rename_but_cannot_edit_content(client, sample_work, monkeypatch):
    from backend.app import llm

    rename = '{"intent": "update", "target": "长城", "field": "title", "value": "金山岭·长城"}'
    monkeypatch.setattr(llm, "raw_chat", lambda *a, **k: rename)
    answer = body(client.post("/ask", json={"question": "把长城的标题改成金山岭·长城"}))
    assert "已把" in answer["answer"]
    assert body(client.get(f"/works/{sample_work['id']}"))["title"] == "金山岭·长城"

    # 换成改内容：策略必须拦住，且正文逐字未变
    edit_content = (
        '{"intent": "update", "target": "金山岭", "field": "content",'
        ' "value": "被 AI 覆盖的内容"}'
    )
    monkeypatch.setattr(llm, "raw_chat", lambda *a, **k: edit_content)
    answer = body(client.post("/ask", json={"question": "把金山岭的内容改成被 AI 覆盖的内容"}))
    assert "不允许通过对话修改" in answer["answer"]
    assert body(client.get(f"/works/{sample_work['id']}"))["content"] == sample_work["content"]


def test_delete_needs_confirm(client, sample_work, monkeypatch):
    from backend.app import llm

    monkeypatch.setattr(
        llm, "raw_chat", lambda *a, **k: '{"intent": "delete", "target": "长城"}'
    )
    preview = body(client.post("/ask", json={"question": "删掉长城那条"}))
    assert "确认" in preview["answer"]
    assert client.get(f"/works/{sample_work['id']}").status_code == 200  # 还没删

    done = body(client.post("/ask", json={"question": "删掉长城那条", "confirm": True}))
    assert "已删除" in done["answer"]
    assert client.get(f"/works/{sample_work['id']}").status_code == 404


def test_upload_stores_copy_and_delete_cleans_it(client):
    body_bytes = "# 笔记\n\n金山岭 19 个镜头。".encode()
    response = client.post(
        "/works/upload",
        files={"file": ("拍摄笔记.md", body_bytes, "text/markdown")},
        data={"tags": "上传,测试"},
    )
    assert response.status_code == 201

    work = body(response)
    assert work["title"] == "拍摄笔记"  # 标题留空时取文件名
    assert work["type"] == "text"
    assert "19 个镜头" in work["content"]
    assert work["tags"] == ["上传", "测试"]

    stored = Path(work["source_path"])
    assert stored.exists(), "上传的文件必须落到 media/ 里"
    assert stored.parent.name == "media"

    assert client.delete(f"/works/{work['id']}").status_code == 200
    assert not stored.exists(), "删除作品要连副本一起清掉"


def test_upload_rejects_unsupported_extension(client):
    """扩展名白名单：先判后缀再落盘，磁盘上不该留下任何东西。"""
    response = client.post(
        "/works/upload",
        files={"file": ("老格式.doc", b"dummy", "application/msword")},
    )
    assert response.status_code == 400
    assert "另存为 .docx" in response.json()["message"]

    exe = client.post(
        "/works/upload",
        files={"file": ("木马.exe", b"MZ\x90\x00", "application/octet-stream")},
    )
    assert exe.status_code == 400
    assert "不支持的文件类型" in exe.json()["message"]

    assert body(client.get("/works")) == []  # 解析失败不能留下半条记录


def test_upload_rejects_fake_image(client):
    """后缀是 .png、内容不是图片：魔数校验要能拦住（扩展名被改过的情况）。"""
    response = client.post(
        "/works/upload",
        files={"file": ("其实是文本.png", b"this is not a png at all", "image/png")},
    )
    assert response.status_code == 400
    assert "内容不像图片" in response.json()["message"]
    assert body(client.get("/works")) == []

    # 落盘之后才发现不对，这半份副本也得清掉
    from backend.app import uploads

    assert list(uploads.MEDIA_DIR.iterdir()) == []


def test_refusal_path_skips_model(client, sample_work):
    """检索没命中：拒绝文案由代码拼，不调用大模型（零 token）。"""
    answer = body(client.post("/ask", json={"question": "这本书讲了什么"}))

    assert answer["sources"] == []
    assert "没有找到" in answer["answer"]
    assert CALLS["chat"] == 0


def test_missing_key_gives_readable_400(client, sample_work, monkeypatch):
    """没填密钥不是 500：给 1002 + 指引，前端据此弹设置窗。"""
    from backend.app import errors, llm

    def no_key(*a, **k):
        raise errors.NotConfigured("还没有填 DeepSeek 密钥，点右上角「设置」填上就能对话了")

    monkeypatch.setattr(llm, "chat", no_key)
    FAKE_HITS.append(
        {
            "text": "金山岭段 19 个镜头",
            "work_id": sample_work["id"],
            "chunk_index": 0,
            "title": "长城拍摄笔记",
            "distance": 0.21,
        }
    )

    response = client.post("/ask", json={"question": "长城拍了多少个镜头"})
    assert response.status_code == 400
    assert response.json()["code"] == 1002
    assert "点右上角「设置」" in response.json()["message"]


def test_search_hit_feeds_catalog_and_chunks(client, sample_work, monkeypatch):
    from backend.app import llm

    FAKE_HITS.append(
        {
            "text": "金山岭段 19 个镜头",
            "work_id": sample_work["id"],
            "chunk_index": 0,
            "title": "长城拍摄笔记",
            "distance": 0.21,
        }
    )
    captured: dict[str, str] = {}

    def spy_chat(question: str, context: str = "") -> str:
        captured["context"] = context
        return "金山岭段拍了 19 个镜头 [1]"

    monkeypatch.setattr(llm, "chat", spy_chat)

    answer = body(client.post("/ask", json={"question": "长城拍了多少个镜头"}))

    assert answer["sources"] == [sample_work["id"]]
    assert "【作品目录】" in captured["context"]
    assert "【相关片段】" in captured["context"]
    assert "[1] 《长城拍摄笔记》" in captured["context"]
    assert "距离 0.21" in captured["context"]


def test_stream_emits_meta_delta_done_in_order(client, sample_work):
    """流式接口是唯一不套信封的：它本身是事件流，套了就没法边生成边发。"""
    FAKE_HITS.append(
        {
            "text": "金山岭段 19 个镜头",
            "work_id": sample_work["id"],
            "chunk_index": 0,
            "title": "长城拍摄笔记",
            "distance": 0.21,
        }
    )
    response = client.post("/ask/stream", json={"question": "长城拍了多少个镜头"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "X-Request-ID" in response.headers  # 流式请求也能对日志

    body_text = response.text
    events = re.findall(r"event: (\w+)", body_text)
    assert events[0] == "meta"
    assert events[-1] == "done"
    assert set(events[1:-1]) == {"delta"}, "中间只允许 delta 事件"

    assert f'"sources": [{sample_work["id"]}]' in body_text
    assert "金山岭段" in body_text  # 模型吐的字逐块发出
    assert CALLS["chat_stream"] == 1
