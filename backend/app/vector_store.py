"""向量库：用 Chroma 持久化存储片段向量。

职责边界：
- 这里只管「向量怎么存取和检索」
- 不负责切片（chunking.py）、不负责调模型（embedding.py）、不负责写业务表（crud.py）

集合只有一个：``assets``，每条记录 = 一个片段。
id 用 ``{asset_id}:{chunk_index}``，这样删改单个素材时可以按元数据精确定位。
"""

import time
from pathlib import Path

import chromadb

from backend.app.config import settings

# 默认落在 backend/chroma_db；容器里由 compose 注入 CHROMA_DIR=/app/data/chroma
_DEFAULT_CHROMA = Path(__file__).resolve().parent.parent / "chroma_db"
CHROMA_DIR = Path(settings.chroma_dir) if settings.chroma_dir else _DEFAULT_CHROMA

COLLECTION = "assets"


def _build_client():
    """两种形态：

    - 嵌入式（本地开发）：PersistentClient 指向一个目录，不需要起任何容器
    - 服务端（容器编排）：HttpClient 连 chroma 服务，向量数据独立持久化，
      后端可以随便删了重建，也允许多实例共享同一份索引

    服务端模式下 HttpClient 构造时就会发 heartbeat，chroma 没起来会抛异常。
    这里加一段重试：即使 compose 的健康检查漏了，后端也不会因为慢半拍就崩。
    """
    if not settings.chroma_host:
        CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        return chromadb.PersistentClient(path=str(CHROMA_DIR))

    last_error: Exception | None = None
    for _ in range(30):
        try:
            client = chromadb.HttpClient(
                host=settings.chroma_host, port=settings.chroma_port
            )
            client.heartbeat()
            return client
        except Exception as exc:  # noqa: BLE001 — 未就绪就等，最后统一报错
            last_error = exc
            time.sleep(1)
    raise RuntimeError(
        f"连不上 Chroma 服务 {settings.chroma_host}:{settings.chroma_port}"
        f"（等了 30 秒）：{last_error}"
    )


_client = _build_client()


def get_collection():
    """拿到（必要时创建）集合。cosine 距离，与已归一化的 bge-m3 向量匹配。"""
    return _client.get_or_create_collection(
        name=COLLECTION,
        metadata={"hnsw:space": "cosine"},
        embedding_function=None,  # 向量由 embedding.py 自己算好再传进来
    )


def upsert_chunks(
    asset_id: int,
    chunks: list[str],
    vectors: list[list[float]],
    meta: dict | None = None,
) -> int:
    """写入一个素材的所有片段，返回写入条数。"""
    if not chunks:
        return 0
    if len(chunks) != len(vectors):
        raise ValueError(f"片段数({len(chunks)})与向量数({len(vectors)})不一致")

    col = get_collection()
    base = meta or {}
    col.upsert(
        ids=[f"{asset_id}:{i}" for i in range(len(chunks))],
        documents=chunks,
        embeddings=vectors,
        metadatas=[
            {**base, "asset_id": asset_id, "chunk_index": i}
            for i in range(len(chunks))
        ],
    )
    return len(chunks)


def delete_asset(asset_id: int) -> None:
    """删除一个素材的全部片段（改内容前必须先删旧的，否则残留旧向量）。"""
    get_collection().delete(where={"asset_id": asset_id})


def query(
    vector: list[float], top_k: int = 5, asset_id: int | None = None
) -> list[dict]:
    """按向量检索，返回 [{text, asset_id, chunk_index, title, projects, distance}, ...]，
    距离越小越相关。"""
    col = get_collection()
    result = col.query(
        query_embeddings=[vector],
        n_results=top_k,
        where={"asset_id": asset_id} if asset_id is not None else None,
    )

    hits: list[dict] = []
    docs = result.get("documents") or [[]]
    metas = result.get("metadatas") or [[]]
    dists = result.get("distances") or [[]]

    for text, meta, dist in zip(docs[0], metas[0], dists[0]):
        hits.append(
            {
                "text": text,
                "asset_id": meta.get("asset_id"),
                "chunk_index": meta.get("chunk_index"),
                "title": meta.get("title"),
                "projects": meta.get("projects") or "",
                "distance": round(float(dist), 4),
            }
        )
    return hits


def count() -> int:
    return get_collection().count()
