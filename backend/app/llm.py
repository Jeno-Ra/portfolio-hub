import time

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from backend.app.config import settings
from backend.app.errors import NotConfigured

MAX_RETRIES = 4
FALLBACK = "抱歉，大模型服务出错，请检查"

# 懒加载：打包版启动时可能一个密钥都没有，这时构造 OpenAI 客户端会直接报错。
# 第一次真正调用才建连接池；用户填完密钥后 reload() 让它重建。
_client: OpenAI | None = None


def _make_client() -> OpenAI:
    if not settings.deepseek_api_key:
        raise NotConfigured("还没有填 DeepSeek 密钥，点右上角「设置」填上就能对话了")
    return OpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        timeout=settings.llm_timeout,
        max_retries=0,  # 关闭 SDK 内置重试，重试权统一由 chat() 控制
    )


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = _make_client()
    return _client


def reload() -> None:
    """密钥变更后调用，下次请求用新密钥重建客户端。"""
    global _client
    _client = None


def ping() -> None:
    """探活：列一次模型列表，不消耗生成额度。密钥填错会在这里抛异常。"""
    _get_client().models.list()

# 助手的身份与工作准则。改这里就能换"人设"，不用动业务代码。
PERSONA = (
    "你是 PortfolioHub 的作品管理助手，一名专业的作品管理人员。"
    "你服务的对象是一位独立创作者，他的资产分五类：视频、图片、文本、代码、项目。\n"
)

RULES = (
    "工作准则：\n"
    "1. 只依据【作品目录】和【相关片段】回答。没有依据时直接说「库里没有相关内容」，"
    "不猜测、不编造、不用常识补充。\n"
    "2. 回答要具体：能给出作品名称、类型、标签、数量就说出来，不要泛泛而谈。\n"
    "3. 用片段作答时，在句末标注来源编号，例如 [1]、[2]。\n"
    "4. 与作品库无关的问题（闲聊、常识、算术），用一句话说明你只负责作品库相关事务。\n"
    "5. 中文、简洁、专业。不要客套话，不要复述用户的问题。\n"
)


def build_system(context: str = "") -> str:
    """拼系统提示词：身份 + 准则 + 数据。没有数据时明确告知，避免模型自由发挥。"""
    if not context:
        return (
            PERSONA
            + RULES
            + "当前没有提供任何作品数据，请如实告知用户：库里还没有作品，需要先录入。\n"
        )
    return PERSONA + RULES + "---- 作品数据 ----\n" + context


def chat(prompt: str, context: str = "") -> str:
    """调 DeepSeek：最多 MAX_RETRIES 次重试，整体不超过总预算，失败返回兜底文案。

    context 是「作品目录 + 检索到的相关片段」，由调用方（main.ask）负责拼装。
    """
    system = build_system(context)

    start = time.monotonic()

    for attempt in range(MAX_RETRIES + 1):
        remaining = settings.llm_total_budget - (time.monotonic() - start)
        if remaining <= 0:
            break

        try:
            resp = _get_client().chat.completions.create(
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


def raw_chat(system: str, prompt: str) -> str:
    """不带作品上下文的原始调用，给意图解析这类"内部任务"用。

    复用同一个 client，但只有一次尝试：解析失败时调用方会回退成 search，
    没必要为了一个可能失败的分类任务重试 4 次。
    """
    resp = _get_client().chat.completions.create(
        model=settings.deepseek_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        timeout=settings.llm_timeout,
    )
    return (resp.choices[0].message.content or "").strip()


def chat_stream(prompt: str, context: str = ""):
    """流式版本：逐块 yield 文本增量。

    与 chat() 的关键差别：**不做重试**。
    流一旦开始输出，重试会导致内容重复（前端已经渲染了前半截），
    所以这里让异常直接抛给调用方，由它转成一个 error 事件。
    """
    system = build_system(context)
    stream = _get_client().chat.completions.create(
        model=settings.deepseek_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        timeout=settings.llm_timeout,
        stream=True,
    )

    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield delta.content
