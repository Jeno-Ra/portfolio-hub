"""接口层测试：用 TestClient 直接打路由，不启服务器、不联网。

覆盖六类行为：
1. 健康检查
2. 作品增删改查 + 404
3. AI 只能改索引信息（标题），改内容被策略拦下
4. 删除必须二次确认
5. 网页上传：落盘 → 解析入库 → 删除时清副本
6. 检索没命中不调模型；命中时把目录与片段喂给模型；流式事件顺序
"""

import re
from pathlib import Path

from backend.tests.fakes import CALLS, FAKE_HITS


def test_runs_on_temporary_database():
    """保护性断言：万一环境变量没生效，测试就会写进真实库——必须先拦住。"""
    from backend.app.database import DB_PATH

    assert "portfolio-hub-tests-" in str(DB_PATH)


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_work_crud(client, sample_work):
    work_id = sample_work["id"]
    assert sample_work["title"] == "长城拍摄笔记"
    assert {tag for tag in sample_work["tags"]} == {"拍摄", "长城"}
    assert sample_work["source_path"] is None  # 手写的作品没有源文件

    assert [w["id"] for w in client.get("/works").json()] == [work_id]

    assert client.get(f"/works/{work_id}").json()["title"] == "长城拍摄笔记"
    assert client.get("/works/9999").status_code == 404

    renamed = client.patch(f"/works/{work_id}", json={"title": "金山岭拍摄笔记"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "金山岭拍摄笔记"
    assert client.patch("/works/9999", json={"title": "x"}).status_code == 404

    assert client.delete(f"/works/{work_id}").status_code == 204
    assert client.get(f"/works/{work_id}").status_code == 404
    assert client.delete(f"/works/{work_id}").status_code == 404  # 删两次，第二次 404


def test_ai_can_rename_but_cannot_edit_content(client, sample_work, monkeypatch):
    from backend.app import llm

    rename = '{"intent": "update", "target": "长城", "field": "title", "value": "金山岭·长城"}'
    monkeypatch.setattr(llm, "raw_chat", lambda *a, **k: rename)
    answer = client.post("/ask", json={"question": "把长城的标题改成金山岭·长城"}).json()
    assert "已把" in answer["answer"]
    assert client.get(f"/works/{sample_work['id']}").json()["title"] == "金山岭·长城"

    # 换成改内容：策略必须拦住，且正文逐字未变
    edit_content = (
        '{"intent": "update", "target": "金山岭", "field": "content",'
        ' "value": "被 AI 覆盖的内容"}'
    )
    monkeypatch.setattr(llm, "raw_chat", lambda *a, **k: edit_content)
    answer = client.post(
        "/ask", json={"question": "把金山岭的内容改成被 AI 覆盖的内容"}
    ).json()
    assert "不允许通过对话修改" in answer["answer"]
    assert (
        client.get(f"/works/{sample_work['id']}").json()["content"]
        == sample_work["content"]
    )


def test_delete_needs_confirm(client, sample_work, monkeypatch):
    from backend.app import llm

    monkeypatch.setattr(
        llm, "raw_chat", lambda *a, **k: '{"intent": "delete", "target": "长城"}'
    )
    preview = client.post("/ask", json={"question": "删掉长城那条"}).json()
    assert "确认" in preview["answer"]
    assert client.get(f"/works/{sample_work['id']}").status_code == 200  # 还没删

    done = client.post("/ask", json={"question": "删掉长城那条", "confirm": True}).json()
    assert "已删除" in done["answer"]
    assert client.get(f"/works/{sample_work['id']}").status_code == 404


def test_upload_stores_copy_and_delete_cleans_it(client):
    body = "# 笔记\n\n金山岭 19 个镜头。".encode()
    response = client.post(
        "/works/upload",
        files={"file": ("拍摄笔记.md", body, "text/markdown")},
        data={"tags": "上传,测试"},
    )
    assert response.status_code == 201
    work = response.json()
    assert work["title"] == "拍摄笔记"  # 标题留空时取文件名
    assert work["type"] == "text"
    assert "19 个镜头" in work["content"]
    assert work["tags"] == ["上传", "测试"]

    stored = Path(work["source_path"])
    assert stored.exists(), "上传的文件必须落到 media/ 里"
    assert stored.parent.name == "media"

    assert client.delete(f"/works/{work['id']}").status_code == 204
    assert not stored.exists(), "删除作品要连副本一起清掉"


def test_unsupported_file_rejected_without_leftover(client):
    response = client.post(
        "/works/upload",
        files={"file": ("老格式.doc", b"dummy", "application/msword")},
    )
    assert response.status_code == 400
    assert "另存为 .docx" in response.json()["detail"]
    assert client.get("/works").json() == []  # 解析失败不能留下半条记录


def test_refusal_path_skips_model(client, sample_work):
    """检索没命中：拒绝文案由代码拼，不调用大模型（零 token）。"""
    answer = client.post("/ask", json={"question": "这本书讲了什么"}).json()

    assert answer["sources"] == []
    assert "没有找到" in answer["answer"]
    assert CALLS["chat"] == 0


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

    answer = client.post("/ask", json={"question": "长城拍了多少个镜头"}).json()

    assert answer["sources"] == [sample_work["id"]]
    assert "【作品目录】" in captured["context"]
    assert "【相关片段】" in captured["context"]
    assert "[1] 《长城拍摄笔记》" in captured["context"]
    assert "距离 0.21" in captured["context"]


def test_stream_emits_meta_delta_done_in_order(client, sample_work):
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

    body = response.text
    events = re.findall(r"event: (\w+)", body)
    assert events[0] == "meta"
    assert events[-1] == "done"
    assert set(events[1:-1]) == {"delta"}, "中间只允许 delta 事件"

    assert f'"sources": [{sample_work["id"]}]' in body
    assert "金山岭段" in body  # 模型吐的字逐块发出
    assert CALLS["chat_stream"] == 1
