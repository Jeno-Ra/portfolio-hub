"""视觉：把图片变成可检索的中文描述。

为什么需要：图片是像素，文本检索看不见它。
这里用视觉模型生成一段客观描述，写进 works.content，
后续切片 / embedding / 检索走的是同一条文本链路——这就是"多模态归一化"。

注意：只做「图 → 字」，不做以图搜图（CLIP），后者在规划里明确砍掉。
"""

import base64
import mimetypes
from pathlib import Path

from openai import APIConnectionError, APITimeoutError, RateLimitError

from backend.app.clients import LazyClient
from backend.app.config import settings

MAX_RETRIES = 2

# 提示词的设计要点：只描述客观内容，禁止评论和猜测。
# 目的是让描述"可被检索"——主体、风格、配色、构图、场景都是可能被搜的词。
VISION_PROMPT = (
    "用中文客观描述这张图，供后续检索使用。请覆盖：主体内容、风格、配色、构图、场景。"
    "150 字以内，只描述看得见的东西，不要评价、不要猜测用途、不要分点。"
)

_client = LazyClient(
    "gpt_api_key",
    "gpt_base_url",
    "vision_timeout",
    "还没有填视觉模型的密钥，点右上角「设置」填上就能描述图片了",
)


def reload() -> None:
    """密钥变更后调用，下次请求用新密钥重建客户端。"""
    _client.reload()


def ping() -> None:
    """探活：列一次模型列表，不消耗生成额度。"""
    _client.get().models.list()


def describe_image(path: str | Path) -> str:
    """读本地图片，返回中文描述。失败会重试 2 次，仍失败抛出异常。"""
    image_path = Path(path)
    if not image_path.is_file():
        raise FileNotFoundError(f"图片不存在：{image_path}")

    mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    data_uri = f"data:{mime};base64,{b64}"

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = _client.get().chat.completions.create(
                model=settings.gpt_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": VISION_PROMPT},
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ],
                    }
                ],
                max_tokens=400,
            )
            return (resp.choices[0].message.content or "").strip()
        except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
            last_error = exc
            if attempt >= MAX_RETRIES:
                break

    raise RuntimeError(f"图片描述生成失败：{last_error}")
