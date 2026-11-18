import time

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from backend.app.config import settings

MAX_RETRIES = 4
FALLBACK = "抱歉，大模型服务出错，请检查"

_client = OpenAI(
    api_key=settings.deepseek_api_key,
    base_url=settings.deepseek_base_url,
    timeout=settings.llm_timeout,
)


def chat(prompt: str) -> str:
    """调 DeepSeek：最多 MAX_RETRIES 次重试，整体不超过总预算，失败返回兜底文案。"""
    start = time.monotonic()

    for attempt in range(MAX_RETRIES + 1):
        remaining = settings.llm_total_budget - (time.monotonic() - start)
        if remaining <= 0:
            break

        try:
            resp = _client.chat.completions.create(
                model=settings.deepseek_model,
                messages=[
                    {"role": "system", "content": "你是一个简洁的中文助手。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                timeout=min(settings.llm_timeout, remaining),
            )
            return resp.choices[0].message.content or ""
        except (RateLimitError, APITimeoutError, APIConnectionError):
            if attempt >= MAX_RETRIES:
                break
            backoff = 0.5 * 2**attempt
            if time.monotonic() - start + backoff >= settings.llm_total_budget:
                break
            time.sleep(backoff)

    return FALLBACK
