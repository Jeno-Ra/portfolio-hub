"""建索引：作品正文 → 切片 → 向量 → 同时写入 Chroma 与 chunks 表。

为什么两处都写：
- Chroma 存向量 + 片段文本，负责"按语义找"
- SQLite 的 chunks 表存片段结构，负责"按作品列出所有切片"这类关系查询
- id 用 {work_id}:{chunk_index}，两边能对上
"""

from sqlalchemy import delete
from sqlalchemy.orm import Session

from backend.app import models, vector_store
from backend.app.chunking import split_text
from backend.app.embedding import embed_texts


def index_work(db: Session, work: models.Work) -> int:
    """重建单个作品的索引，返回片段数。

    先删后建：作品内容改了以后旧片段必须清掉，
    否则检索会召回已经不存在的内容（很难察觉的那种 bug）。
    """
    vector_store.delete_work(work.id)
    db.execute(delete(models.Chunk).where(models.Chunk.work_id == work.id))
    db.commit()

    chunks = split_text(work.content or "")
    if not chunks:
        return 0

    vectors = embed_texts(chunks)
    vector_store.upsert_chunks(
        work.id,
        chunks,
        vectors,
        meta={"title": work.title, "type": work.type},
    )

    for index, content in enumerate(chunks):
        db.add(models.Chunk(work_id=work.id, content=content, chunk_index=index))
    db.commit()

    return len(chunks)
