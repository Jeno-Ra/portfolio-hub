"""检索链路的测试：阈值过滤、top-k 传参、引用编号、sources 去重。

真正的向量库不参与测试——embedding 与 Chroma 都换成了假实现（见 fakes.py）。
这样测的是"我们自己的过滤与拼装逻辑"，而不是第三方库的行为；
换 embedding 模型或换向量库时，这些用例不需要改写。
"""

from backend.app import embedding, qa_api, vector_store
from backend.tests.conftest import body
from backend.tests.fakes import CALLS, FAKE_HITS


def make_hit(
    asset_id: int, chunk_index: int, distance: float, **kwargs
) -> dict:
    """构造一条检索命中；字段名必须与 vector_store.query 的返回一致。"""
    hit = {
        "text": "金山岭段 19 个镜头",
        "asset_id": asset_id,
        "chunk_index": chunk_index,
        "title": "长城拍摄笔记",
        "projects": "王者无界嬴政",
        "distance": distance,
    }
    hit.update(kwargs)
    return hit


def test_retrieve_keeps_only_hits_within_threshold(monkeypatch):
    """top-k 永远会凑满 k 条，所以必须按距离过滤，否则引用里会混进无关素材。"""
    hits = [
        make_hit(1, 0, 0.100),
        make_hit(1, 1, 0.449),
        make_hit(2, 0, 0.450),  # 阈值本身算命中（<=）
        make_hit(2, 1, 0.451),
        make_hit(3, 0, 0.620),
    ]
    monkeypatch.setattr(vector_store, "query", lambda *args, **kwargs: hits)

    kept = qa_api._retrieve("长城拍了什么")

    assert [hit["distance"] for hit in kept] == [0.100, 0.449, 0.450]
    assert [hit["asset_id"] for hit in kept] == [1, 1, 2]  # 原有顺序不能被重排


def test_retrieve_uses_top_k_and_question_vector(monkeypatch):
    """检索必须用"问题"的向量、按配置的 k 去查——传错任何一个都会静默变差。"""
    seen: dict[str, object] = {}

    def spy_query(vector, top_k=0):
        seen["vector"] = vector
        seen["top_k"] = top_k
        return []

    monkeypatch.setattr(vector_store, "query", spy_query)
    monkeypatch.setattr(embedding, "embed_text", lambda question: [0.5] * 1024)

    assert qa_api._retrieve("长城拍了什么") == []
    assert seen["top_k"] == qa_api.RETRIEVAL_TOP_K == 5
    assert seen["vector"] == [0.5] * 1024


def test_all_hits_filtered_out_falls_back_to_refusal(client, sample_asset):
    """5 条命中但全部超阈值：等于没找到，不能调模型，也不能给引用。"""
    FAKE_HITS.extend(make_hit(999, index, 0.60) for index in range(5))

    data = body(client.post("/ask", json={"question": "长城拍了多少个镜头"}))

    assert data["sources"] == []
    assert "没有找到" in data["answer"]
    assert "王者无界嬴政" in data["answer"]  # 拒绝时也告诉用户库里有哪些项目
    assert CALLS["chat"] == 0


def test_sources_dedup_chunks_of_same_asset(client, sample_asset):
    """一条素材切成多片会同时命中，引用里只能出现一次。"""
    other = body(
        client.post(
            "/assets",
            json={"title": "茶室空镜", "type": "text", "content": "午后茶室的空镜素材。"},
        )
    )

    FAKE_HITS.extend(
        [
            make_hit(sample_asset["id"], 0, 0.20),
            make_hit(sample_asset["id"], 1, 0.30),
            make_hit(other["id"], 0, 0.40),
        ]
    )

    data = body(client.post("/ask", json={"question": "长城那天的素材"}))

    assert [s["asset_id"] for s in data["sources"]] == [sample_asset["id"], other["id"]]  # 去重且保序


def test_context_numbers_chunks_in_the_same_order_as_sources():
    """上下文里的 [1] [2] 必须与 sources 顺序一致，否则回答会引用错素材。"""
    hits = [
        make_hit(1, 0, 0.2, text="甲段内容"),
        make_hit(2, 3, 0.3, title="茶室空镜", projects="", text="乙段内容"),
    ]

    context = qa_api._chunks_context(hits)

    # 有项目时写成《项目 · 素材》，没项目时只写素材名
    assert context.startswith("[1] 《王者无界嬴政 · 长城拍摄笔记》第 0 段（距离 0.2）")
    assert "[2] 《茶室空镜》第 3 段（距离 0.3）" in context
    assert "甲段内容" in context and "乙段内容" in context


def test_rag_context_contains_catalog_and_chunks(client, sample_asset):
    """模型既要看到"库里有什么"（项目目录），也要看到"这段说了什么"（片段）。"""
    from backend.app.database import SessionLocal

    with SessionLocal() as db:
        context = qa_api._rag_context(db, [make_hit(sample_asset["id"], 0, 0.2)])

    assert "【项目与素材】" in context
    assert "【项目】王者无界嬴政" in context
    assert "- [文本] 长城拍摄笔记｜标签：拍摄、长城" in context
    assert "【相关片段】" in context
    assert "金山岭段 19 个镜头" in context
