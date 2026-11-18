import time

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from backend.app.config import settings

MAX_RETRIES = 4
FALLBACK = "抱歉，大模型服务出错，请检查"

_client = OpenAI(
    api_key=settings.deepseek_api_key,
    base_url=settings.deepseek_base_url,
    timeout=settings.llm_timeout,
    max_retries=0,  # 关闭 SDK 内置重试，重试权统一由 chat() 控制
)


def chat(prompt: str, context: str = "") -> str:
    """调 DeepSeek：最多 MAX_RETRIES 次重试，整体不超过总预算，失败返回兜底文案。

    context 是作品库摘要：给了就按"作品库助手"回答，
    没给就是普通问答（第 6 周会把它换成向量检索的 top-k 结果）。
    """
    if context:
        system = (
            "你是个人作品库助手。只能依据下面列出的作品回答；"
            "列表里没有的内容，直接说「库里没有相关作品」，绝不编造。\n"
            "---- 作品列表 ----\n"
            f"{context}"
        )
    else:
        system = "你是一个简洁的中文助手。"

    start = time.monotonic()

    for attempt in range(MAX_RETRIES + 1):
        remaining = settings.llm_total_budget - (time.monotonic() - start)
        if remaining <= 0:
            break

        try:
            resp = _client.chat.completions.create(
                model=settings.deepseek_model,
                messages=[
                    {"role": "system", "content": system},
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
