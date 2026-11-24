"""建索引：素材正文 → 切片 → 向量 → 同时写入 Chroma 与 chunks 表。

为什么两处都写：
- Chroma 存向量 + 片段文本，负责"按语义找"
- 关系库存片段结构，负责"按素材列出所有切片"这类关系查询
- id 用 {asset_id}:{chunk_index}，两边能对上
"""

from sqlalchemy import delete
from sqlalchemy.orm import Session

from backend.app import errors, models, vector_store
from backend.app.chunking import split_text
from backend.app.embedding import embed_texts


def index_quietly(db: Session, asset: models.Asset) -> None:
    """建索引，但没配向量化密钥时不阻塞主流程。

    打包版第一次运行往往还没有密钥：这时素材要能先存下来，
    等用户在设置里填好密钥、保存时再统一补建（见 settings_api.update_settings）。
    """
    try:
        index_asset(db, asset)
    except errors.NotConfigured:
        pass


def index_asset(db: Session, asset: models.Asset) -> int:
    """重建单个素材的索引，返回片段数。

    先删后建：素材内容改了以后旧片段必须清掉，
    否则检索会召回已经不存在的内容（很难察觉的那种 bug）。
    """
    # 先完成切片和向量化，再触碰旧索引。这样密钥缺失、网络错误或
    # 维度不匹配时，旧索引仍可用，不会出现“素材存在但完全搜不到”。
    chunks = split_text(asset.content or "")
    if not chunks:
        vector_store.delete_asset(asset.id)
        db.execute(delete(models.Chunk).where(models.Chunk.asset_id == asset.id))
        db.commit()
        return 0

    vectors = embed_texts(chunks)
    vector_store.delete_asset(asset.id)
    db.execute(delete(models.Chunk).where(models.Chunk.asset_id == asset.id))
    db.commit()

    vector_store.upsert_chunks(
        asset.id,
        chunks,
        vectors,
        meta={
            "title": asset.title,
            "type": asset.type,
            # 素材可能同时属于多个项目，检索结果里要能说清"这是哪个项目里的东西"
            "projects": "、".join(project.title for project in asset.projects),
        },
    )

    for index, content in enumerate(chunks):
        db.add(models.Chunk(asset_id=asset.id, content=content, chunk_index=index))
    db.commit()

    return len(chunks)
