"""向量化：把文本变成向量，供 Chroma 做语义检索。

为什么单独一层：
- 业务代码只说"给我这些片段的向量"，不关心是哪家服务、什么模型
- 以后换模型（比如换 Qwen3-Embedding）只改这里 + 重建向量库
"""

import time

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from backend.app.config import settings

MAX_RETRIES = 3
FALLBACK_BACKOFF = 0.5

_client = OpenAI(
    api_key=settings.embedding_api_key,
    base_url=settings.embedding_base_url,
    timeout=settings.llm_timeout,
    max_retries=0,  # 与 llm.py 一致：重试权归本模块，避免双层重试
)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化，返回顺序与输入严格一致。

    按 embedding_batch 分批：一次提交几十条比逐条快一个数量级，
    也不会因为单次请求过大被服务端拒绝。
    """
    if not texts:
        return []

    vectors: list[list[float]] = []
    batch_size = settings.embedding_batch

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        vectors.extend(_embed_batch(batch))

    # 维度守卫：换模型忘了改 embedding_dim 时，这里立刻报错，
    # 而不是等到写进 Chroma 才因为维度不匹配炸掉
    actual = len(vectors[0])
    if actual != settings.embedding_dim:
        raise ValueError(
            f"向量维度不符：模型返回 {actual} 维，配置 embedding_dim={settings.embedding_dim}。"
            f"换过 embedding 模型就必须同步改 .env 并重建向量库。"
        )

    return vectors


def embed_text(text: str) -> list[float]:
    """单条向量化（检索时 embed 用户问题用这个）。"""
    vectors = embed_texts([text])
    return vectors[0]


def _embed_batch(batch: list[str]) -> list[list[float]]:
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = _client.embeddings.create(
                model=settings.embedding_model,
                input=batch,
            )
            # 服务端不保证顺序，按 index 还原，否则片段和向量会错位
            ordered = sorted(resp.data, key=lambda d: d.index)
            return [item.embedding for item in ordered]
        except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
            last_error = exc
            if attempt >= MAX_RETRIES:
                break
            time.sleep(FALLBACK_BACKOFF * 2**attempt)

    raise RuntimeError(f"向量化失败，已重试 {MAX_RETRIES} 次：{last_error}")
