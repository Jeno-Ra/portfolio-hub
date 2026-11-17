from openai import OpenAI

from backend.app.config import settings

_client = OpenAI(
    api_key=settings.deepseek_api_key,
    base_url=settings.deepseek_base_url,
    timeout=settings.llm_timeout,
)


def chat(prompt: str) -> str:
    """调一次 DeepSeek，返回纯文本答案。超时由客户端统一控制（30 s）。"""
    resp = _client.chat.completions.create(
        model=settings.deepseek_model,
        messages=[
            {"role": "system", "content": "你是一个简洁的中文助手。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
    )
    return resp.choices[0].message.content or ""
