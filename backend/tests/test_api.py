"""接口层测试：用 TestClient 直接打路由，不启服务器、不联网。

覆盖八类行为：
1. 统一返回格式（成功信封、错误信封、请求号）
2. 项目增删改查 + 404；封面单独上传
3. 素材增删改查；挂到项目 / 从项目摘除；删项目不删素材
4. AI 只能改索引信息（标题），改内容被策略拦下
5. 删除必须二次确认
6. 网页上传：按扩展名判类型（图片/音频/文本）、落盘 → 解析入库 → 删除时清副本；
   扩展名与图片魔数两道校验
7. 检索没命中不调模型；命中时把项目目录与片段喂给模型；流式事件顺序
8. 缺密钥时给可读的 400（而不是 500）
"""

import base64
import re
from pathlib import Path

from backend.tests.conftest import body
from backend.tests.fakes import CALLS, DESCRIPTION, FAKE_HITS, hit

# 1x1 的 PNG：够过魔数校验，不需要真的能看
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


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
    response = client.get("/projects")
    payload = response.json()

    assert set(payload) == {"code", "message", "data"}
    assert payload["code"] == 0
    assert payload["message"] == "共 0 个项目"  # message 是给人看的，不是固定 "ok"

    request_id = response.headers["X-Request-ID"]
    assert re.fullmatch(r"[0-9a-f]{8}", request_id)


def test_error_envelopes(client):
    """错误也长成信封：404 是 1003，422 是 1005 且能指出哪个字段。"""
    missing = client.get("/projects/9999")
    assert missing.status_code == 404
    assert missing.json()["code"] == 1003
    assert missing.json()["data"] is None

    invalid = client.post("/ask", json={})
    assert invalid.status_code == 422
    payload = invalid.json()
    assert payload["code"] == 1005
    assert "参数错误" in payload["message"]
    assert "question" in payload["message"]  # 说清楚是哪个字段缺了


def test_project_crud(client, sample_project):
    project_id = sample_project["id"]
    assert sample_project["title"] == "王者无界嬴政"
    assert sample_project["summary"].startswith("记录片项目")
    assert set(sample_project["tags"]) == {"商业", "记录片"}
    assert sample_project["asset_count"] == 0
    assert sample_project["cover_url"] is None  # 没传封面就没有封面

    assert [p["id"] for p in body(client.get("/projects"))] == [project_id]
    assert body(client.get(f"/projects/{project_id}"))["assets"] == []

    renamed = client.patch(f"/projects/{project_id}", json={"title": "王者无界·嬴政"})
    assert renamed.status_code == 200
    assert body(renamed)["title"] == "王者无界·嬴政"
    assert client.patch("/projects/9999", json={"title": "x"}).status_code == 404

    # 删除走 200 + 信封（204 不能带响应体，前端就没法统一按 code 判断）
    deleted = client.delete(f"/projects/{project_id}")
    assert deleted.status_code == 200
    assert body(deleted)["id"] == project_id
    assert client.get(f"/projects/{project_id}").status_code == 404
    assert client.delete(f"/projects/{project_id}").status_code == 404  # 删两次，第二次 404


def test_project_cover_is_uploaded_and_served(client, sample_project):
    """封面可选：传了要能通过 /media 在浏览器里看到。"""
    response = client.post(
        f"/projects/{sample_project['id']}/cover",
        files={"file": ("封面.png", PNG, "image/png")},
    )
    assert response.status_code == 200

    cover_url = body(response)["cover_url"]
    assert cover_url.startswith("/media/")

    served = client.get(cover_url)
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"

    # 不是图片的东西当封面要拦住
    bad = client.post(
        f"/projects/{sample_project['id']}/cover",
        files={"file": ("假的.png", b"not a png", "image/png")},
    )
    assert bad.status_code == 400
    assert "内容不像图片" in bad.json()["message"]


def test_asset_crud(client, sample_project, sample_asset):
    asset_id = sample_asset["id"]
    assert sample_asset["title"] == "长城拍摄笔记"
    assert {tag for tag in sample_asset["tags"]} == {"拍摄", "长城"}
    assert sample_asset["project_ids"] == [sample_project["id"]]
    assert sample_asset["source_path"] is None  # 手写的素材没有源文件

    assert [a["id"] for a in body(client.get("/assets"))] == [asset_id]
    # 按项目 / 按类型过滤都要能用（前端左侧筛选就是这两条）
    assert [a["id"] for a in body(client.get(f"/assets?project_id={sample_project['id']}"))] == [
        asset_id
    ]
    assert body(client.get("/assets?kind=video")) == []

    patched = client.patch(f"/assets/{asset_id}", json={"title": "金山岭拍摄笔记"})
    assert patched.status_code == 200
    assert body(patched)["title"] == "金山岭拍摄笔记"
    assert client.patch("/assets/9999", json={"title": "x"}).status_code == 404

    deleted = client.delete(f"/assets/{asset_id}")
    assert deleted.status_code == 200
    assert client.get(f"/assets/{asset_id}").status_code == 404


def test_link_and_unlink_asset(client, sample_project, sample_asset):
    """素材可以复用到多个项目；摘除只是断开关联，素材本身还在。"""
    other = body(client.post("/projects", json={"title": "2026 合集"}))
    linked = client.post(
        f"/projects/{other['id']}/assets", json={"asset_ids": [sample_asset["id"]]}
    )
    assert linked.status_code == 200
    assert body(linked)["added"] == 1

    detail = body(client.get(f"/projects/{other['id']}"))
    assert [a["id"] for a in detail["assets"]] == [sample_asset["id"]]

    # 重复挂同一个不报错，但也不重复计数
    again = client.post(
        f"/projects/{other['id']}/assets", json={"asset_ids": [sample_asset["id"]]}
    )
    assert body(again)["added"] == 0

    removed = client.delete(f"/projects/{other['id']}/assets/{sample_asset['id']}")
    assert removed.status_code == 200
    assert body(client.get(f"/projects/{other['id']}"))["assets"] == []
    # 原项目那边还挂着，素材没被删
    assert body(client.get(f"/assets/{sample_asset['id']}"))["id"] == sample_asset["id"]


def test_delete_project_keeps_assets_unless_asked(client, sample_project, sample_asset):
    """默认只删容器：素材可能还挂在别的项目下，不能跟着一起没。"""
    assert body(client.delete(f"/projects/{sample_project['id']}"))["removed_assets"] == 0
    assert client.get(f"/assets/{sample_asset['id']}").status_code == 200

    # 明确要求连素材一起删时才删，而且连 media/ 里的副本也要清掉
    second = body(client.post("/projects", json={"title": "临时项目"}))
    doomed = body(
        client.post(
            "/assets/upload",
            files={"file": ("要一起删.txt", "临时".encode(), "text/plain")},
            data={"project_id": str(second["id"])},
        )
    )
    stored = Path(doomed["source_path"])
    assert stored.exists()

    deleted = body(client.delete(f"/projects/{second['id']}?with_assets=true"))
    assert deleted["removed_assets"] == 1
    assert not stored.exists(), "删项目连素材一起删时，上传副本不能留在 media/ 里"
    assert [a["title"] for a in body(client.get("/assets"))] == ["长城拍摄笔记"]


def test_ai_can_rename_but_cannot_edit_content(client, sample_asset, monkeypatch):
    from backend.app import llm

    rename = '{"intent": "update", "target": "长城", "field": "title", "value": "金山岭·长城"}'
    monkeypatch.setattr(llm, "raw_chat", lambda *a, **k: rename)
    answer = body(client.post("/ask", json={"question": "把长城的标题改成金山岭·长城"}))
    assert "已把" in answer["answer"]
    assert body(client.get(f"/assets/{sample_asset['id']}"))["title"] == "金山岭·长城"

    # 换成改内容：策略必须拦住，且正文逐字未变
    edit_content = (
        '{"intent": "update", "target": "金山岭", "field": "content",'
        ' "value": "被 AI 覆盖的内容"}'
    )
    monkeypatch.setattr(llm, "raw_chat", lambda *a, **k: edit_content)
    answer = body(client.post("/ask", json={"question": "把金山岭的内容改成被 AI 覆盖的内容"}))
    assert "不允许通过对话修改" in answer["answer"]
    assert (
        body(client.get(f"/assets/{sample_asset['id']}"))["content"]
        == sample_asset["content"]
    )


def test_delete_needs_confirm(client, sample_asset, monkeypatch):
    from backend.app import llm

    monkeypatch.setattr(
        llm, "raw_chat", lambda *a, **k: '{"intent": "delete", "target": "长城"}'
    )
    preview = body(client.post("/ask", json={"question": "删掉长城那条"}))
    assert "确认" in preview["answer"]
    assert client.get(f"/assets/{sample_asset['id']}").status_code == 200  # 还没删

    done = body(client.post("/ask", json={"question": "删掉长城那条", "confirm": True}))
    assert "已删除" in done["answer"]
    assert client.get(f"/assets/{sample_asset['id']}").status_code == 404


def test_upload_stores_copy_and_delete_cleans_it(client, sample_project):
    body_bytes = "# 笔记\n\n金山岭 19 个镜头。".encode()
    response = client.post(
        "/assets/upload",
        files={"file": ("拍摄笔记.md", body_bytes, "text/markdown")},
        data={"tags": "上传,测试", "project_id": str(sample_project["id"])},
    )
    assert response.status_code == 201

    asset = body(response)
    assert asset["title"] == "拍摄笔记"  # 标题留空时取文件名
    assert asset["type"] == "text"
    assert "19 个镜头" in asset["content"]
    assert asset["tags"] == ["上传", "测试"]
    assert asset["project_ids"] == [sample_project["id"]]  # 上传时直接挂进项目

    stored = Path(asset["source_path"])
    assert stored.exists(), "上传的文件必须落到 media/ 里"
    assert stored.parent.name == "media"

    assert client.delete(f"/assets/{asset['id']}").status_code == 200
    assert not stored.exists(), "删除素材要连副本一起清掉"


def test_upload_strips_bom_and_rejects_unknown_project(client, sample_project):
    """Windows 记事本存的文件带 BOM；项目 id 不存在时不能假装挂上了。"""
    with_bom = "\ufeff# 带 BOM 的笔记\n\n正文".encode("utf-8")
    asset = body(
        client.post(
            "/assets/upload",
            files={"file": ("bom.txt", with_bom, "text/plain")},
            data={"project_id": str(sample_project["id"])},
        )
    )
    assert asset["content"].startswith("# 带 BOM 的笔记"), "BOM 不该留在正文里"

    missing = client.post(
        "/assets/upload",
        files={"file": ("孤儿.txt", b"content", "text/plain")},
        data={"project_id": "9999"},
    )
    assert missing.status_code == 404
    assert "项目不存在" in missing.json()["message"]


def test_upload_decides_kind_from_file_itself(client):
    """类型不用用户选：图片自动走视觉模型，音频只存手填描述。"""
    image = body(
        client.post(
            "/assets/upload",
            files={"file": ("晨雾.png", PNG, "image/png")},
        )
    )
    assert image["type"] == "image"
    assert image["content"] == DESCRIPTION  # 描述来自（假的）视觉模型

    audio = body(
        client.post(
            "/assets/upload",
            files={"file": ("片头曲.mp3", b"ID3\x03\x00\x00\x00", "audio/mpeg")},
            data={"notes": "3 分钟，古筝打底，中段加了鼓点"},
        )
    )
    assert audio["type"] == "audio"
    assert "古筝" in audio["content"]  # 素材描述就是用户填的那句

    silent = body(
        client.post(
            "/assets/upload",
            files={"file": ("未命名音频.wav", b"RIFF\x24\x00\x00\x00", "audio/wav")},
        )
    )
    assert silent["type"] == "audio"
    assert silent["content"] == "（暂无描述）"


def test_upload_rejects_unsupported_extension(client):
    """扩展名白名单：先判后缀再落盘，磁盘上不该留下任何东西。"""
    response = client.post(
        "/assets/upload",
        files={"file": ("老格式.doc", b"dummy", "application/msword")},
    )
    assert response.status_code == 400
    assert "另存为 .docx" in response.json()["message"]

    exe = client.post(
        "/assets/upload",
        files={"file": ("木马.exe", b"MZ\x90\x00", "application/octet-stream")},
    )
    assert exe.status_code == 400
    assert "不支持的文件类型" in exe.json()["message"]

    assert body(client.get("/assets")) == []  # 解析失败不能留下半条记录


def test_upload_rejects_fake_image(client):
    """后缀是 .png、内容不是图片：魔数校验要能拦住（扩展名被改过的情况）。"""
    response = client.post(
        "/assets/upload",
        files={"file": ("其实是文本.png", b"this is not a png at all", "image/png")},
    )
    assert response.status_code == 400
    assert "内容不像图片" in response.json()["message"]
    assert body(client.get("/assets")) == []

    # 落盘之后才发现不对，这半份副本也得清掉
    from backend.app import uploads

    assert list(uploads.MEDIA_DIR.iterdir()) == []


def test_refusal_path_skips_model(client, sample_asset):
    """检索没命中：拒绝文案由代码拼，不调用大模型（零 token）。"""
    answer = body(client.post("/ask", json={"question": "这本书讲了什么"}))

    assert answer["sources"] == []
    assert "没有找到" in answer["answer"]
    assert CALLS["chat"] == 0


def test_missing_key_gives_readable_400(client, sample_asset, monkeypatch):
    """没填密钥不是 500：给 1002 + 指引，前端据此弹设置窗。"""
    from backend.app import errors, llm

    def no_key(*a, **k):
        raise errors.NotConfigured("还没有填 DeepSeek 密钥，点右上角「设置」填上就能对话了")

    monkeypatch.setattr(llm, "chat", no_key)
    FAKE_HITS.append(hit(sample_asset))

    response = client.post("/ask", json={"question": "长城拍了多少个镜头"})
    assert response.status_code == 400
    assert response.json()["code"] == 1002
    assert "点右上角「设置」" in response.json()["message"]


def test_search_hit_feeds_catalog_and_chunks(client, sample_asset, monkeypatch):
    from backend.app import llm

    FAKE_HITS.append(hit(sample_asset))
    captured: dict[str, str] = {}

    def spy_chat(question: str, context: str = "") -> str:
        captured["context"] = context
        return "金山岭段拍了 19 个镜头 [1]"

    monkeypatch.setattr(llm, "chat", spy_chat)

    answer = body(client.post("/ask", json={"question": "长城拍了多少个镜头"}))

    assert [s["asset_id"] for s in answer["sources"]] == [sample_asset["id"]]
    assert "【项目与素材】" in captured["context"]
    assert "【项目】王者无界嬴政" in captured["context"]  # 目录按项目分组
    assert "【相关片段】" in captured["context"]
    # 引用要能看出素材属于哪个项目，不能只给素材名
    assert "[1] 《王者无界嬴政 · 长城拍摄笔记》" in captured["context"]
    assert "距离 0.21" in captured["context"]


def test_stream_emits_meta_delta_done_in_order(client, sample_asset):
    """流式接口是唯一不套信封的：它本身是事件流，套了就没法边生成边发。"""
    FAKE_HITS.append(hit(sample_asset))
    response = client.post("/ask/stream", json={"question": "长城拍了多少个镜头"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "X-Request-ID" in response.headers  # 流式请求也能对日志

    text = response.text
    events = re.findall(r"event: (\w+)", text)
    assert events[0] == "meta"
    assert events[-1] == "done"
    assert set(events[1:-1]) == {"delta"}, "中间只允许 delta 事件"

    assert '"asset_id"' in text
    assert "金山岭段" in text  # 模型吐的字逐块发出
    assert CALLS["chat_stream"] == 1
