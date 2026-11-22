"""测试用的假外部依赖。

真实现会联网（embedding、对话、视觉）并写真实向量库，跑一次测试要花钱还要等超时，
CI 上更不可能过。所以统一换成本地假实现，顺便记录调用次数——
"没调用模型"这件事本身就是被测行为（拒绝路径零 token）。
"""

from collections.abc import Iterator

# 检索命中列表：用例往里塞就命中，留空就走拒绝路径
FAKE_HITS: list[dict] = []

# 各类外部调用的次数，用例用它断言某条路径根本没花 token
CALLS: dict[str, int] = {"chat": 0, "chat_stream": 0, "raw_chat": 0, "embed": 0}

REPLY = "金山岭段拍了 19 个镜头 [1]"


def reset() -> None:
    FAKE_HITS.clear()
    for key in CALLS:
        CALLS[key] = 0


def fake_chat(question: str, context: str = "") -> str:
    CALLS["chat"] += 1
    return REPLY


def fake_chat_stream(question: str, context: str = "") -> Iterator[str]:
    CALLS["chat_stream"] += 1
    yield "金山岭段"
    yield "拍了 19 个镜头 [1]"


def fake_raw_chat(system: str, prompt: str) -> str:
    CALLS["raw_chat"] += 1
    return '{"intent": "search"}'


def fake_embed(text: str) -> list[float]:
    CALLS["embed"] += 1
    return [0.0] * 1024


def fake_query(*args, **kwargs) -> list[dict]:
    return list(FAKE_HITS)
