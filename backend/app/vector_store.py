"""向量库：用 Chroma 持久化存储片段向量。

职责边界：
- 这里只管「向量怎么存取和检索」
- 不负责切片（chunking.py）、不负责调模型（embedding.py）、不负责写业务表（crud.py）

集合只有一个：``works``，每条记录 = 一个片段。
id 用 ``{work_id}:{chunk_index}``，这样删改单个作品时可以按前缀/元数据精确定位。
"""

from pathlib import Path

import chromadb

CHROMA_DIR = Path(__file__).resolve().parent.parent / "chroma_db"
COLLECTION = "works"

_client = chromadb.PersistentClient(path=str(CHROMA_DIR))


def get_collection():
    """拿到（必要时创建）集合。cosine 距离，与已归一化的 bge-m3 向量匹配。"""
    return _client.get_or_create_collection(
        name=COLLECTION,
        metadata={"hnsw:space": "cosine"},
        embedding_function=None,  # 向量由 embedding.py 自己算好再传进来
    )


def upsert_chunks(
    work_id: int,
    chunks: list[str],
    vectors: list[list[float]],
    meta: dict | None = None,
) -> int:
    """写入一个作品的所有片段，返回写入条数。"""
    if not chunks:
        return 0
    if len(chunks) != len(vectors):
        raise ValueError(f"片段数({len(chunks)})与向量数({len(vectors)})不一致")

    col = get_collection()
    base = meta or {}
    col.upsert(
        ids=[f"{work_id}:{i}" for i in range(len(chunks))],
        documents=chunks,
        embeddings=vectors,
        metadatas=[
            {**base, "work_id": work_id, "chunk_index": i} for i in range(len(chunks))
        ],
    )
    return len(chunks)


def delete_work(work_id: int) -> None:
    """删除一个作品的全部片段（改内容前必须先删旧的，否则残留旧向量）。"""
    get_collection().delete(where={"work_id": work_id})


def query(
    vector: list[float], top_k: int = 5, work_id: int | None = None
) -> list[dict]:
    """按向量检索，返回 [{text, work_id, chunk_index, title, distance}, ...]，距离越小越相关。"""
    col = get_collection()
    result = col.query(
        query_embeddings=[vector],
        n_results=top_k,
        where={"work_id": work_id} if work_id is not None else None,
    )

    hits: list[dict] = []
    docs = result.get("documents") or [[]]
    metas = result.get("metadatas") or [[]]
    dists = result.get("distances") or [[]]

    for text, meta, dist in zip(docs[0], metas[0], dists[0]):
        hits.append(
            {
                "text": text,
                "work_id": meta.get("work_id"),
                "chunk_index": meta.get("chunk_index"),
                "title": meta.get("title"),
                "distance": round(float(dist), 4),
            }
        )
    return hits


def count() -> int:
    return get_collection().count()
