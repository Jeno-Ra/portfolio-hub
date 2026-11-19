"""意图路由：把自然语言指令解析成结构化动作。

为什么需要它：
现在的 /ask 只会「搜索」。用户说「把明长城那条标题改成 X」时，
系统只会检索到那条作品然后描述它，不会真的改。

解析结果形如：
    {"intent": "update", "target": "明长城", "field": "title", "value": "新标题"}

五类意图：
- search  查找/了解内容（默认，走原来的 RAG 链路）
- stats   统计数量（纯 SQL，不需要模型）
- create  新增作品
- update  修改标题/类型/内容/标签
- delete  删除（必须二次确认，不可逆）

已知取舍：解析靠一次轻量 LLM 调用，失败时**回退成 search**。
宁可多检索一次，也不要因为解析失败而误改/误删数据。
"""

import json
import re

from backend.app import llm

INTENTS = ("search", "stats", "create", "update", "delete")

ROUTER_SYSTEM = """你是作品库的指令解析器。把用户的话解析成一个 JSON 对象，只输出 JSON，不要任何解释。

字段：
- intent: search / stats / create / update / delete
- target: 要操作的作品标题关键词（没有就给空串）
- field: update 时改哪个字段，只能是 title / type / content / tags（否则空串）
- value: update 时的新值（否则空串）
- title: create 时的标题（否则空串）
- content: create 时的内容（否则空串）
- tags: create 时的标签数组（没有就给空数组）

判断规则：
- 问「有哪些/多少/几个/统计」→ stats
- 说「改成/改一下/重命名/换成」→ update
- 说「删掉/删除/去掉」→ delete
- 说「记一条/新增/添加/记录一下」→ create
- 其余（询问内容、找东西）→ search
"""

FALLBACK = {
    "intent": "search",
    "target": "",
    "field": "",
    "value": "",
    "title": "",
    "content": "",
    "tags": [],
}

# 关键词先筛一遍：命不中就直接当 search，**不调用模型**。
# 理由：如果每条「明长城在哪拍的」都要多一次分类调用，
# 搜索的首字延迟会从 0.9 秒涨到 1.5 秒以上——为了管理指令牺牲搜索体验不划算。
KEYWORD_HINTS = {
    "stats": ("多少条", "几条", "多少个", "几类作品", "统计"),
    "update": (
        "改成",
        "改为",
        "重命名",
        "换成",
        "修改",
        "加标签",
        "添加标签",
        "打个标签",
    ),
    "delete": ("删掉", "删除", "移除", "去掉"),
    "create": ("记一条", "新增一条", "添加一条", "加一条", "记录一下"),
}


def _keyword_intent(question: str) -> str | None:
    for intent, words in KEYWORD_HINTS.items():
        if any(word in question for word in words):
            return intent
    return None


def route(question: str) -> dict:
    """解析用户意图。没有管理指令的关键词时直接返回 search（零模型调用）。"""
    hint = _keyword_intent(question)
    if hint is None:
        return dict(FALLBACK)

    # 有关键词再让模型抽槽位；把预期意图告诉它，减少误判
    prompt = (
        f"用户这句话里含有「{hint}」类指令，请按这个意图解析并抽取字段：\n{question}"
    )
    try:
        raw = llm.raw_chat(ROUTER_SYSTEM, prompt)
    except Exception:
        return dict(FALLBACK)

    data = _parse_json(raw)
    if data is None:
        return {**FALLBACK, "intent": hint}

    intent = str(data.get("intent", hint)).strip().lower()
    if intent not in INTENTS:
        intent = hint

    return {
        "intent": intent,
        "target": str(data.get("target") or "").strip(),
        "field": str(data.get("field") or "").strip(),
        "value": str(data.get("value") or "").strip(),
        "title": str(data.get("title") or "").strip(),
        "content": str(data.get("content") or "").strip(),
        "tags": [str(t).strip() for t in (data.get("tags") or []) if str(t).strip()],
    }


def _parse_json(raw: str) -> dict | None:
    """模型偶尔会包一层 ```json 代码块，这里剥掉再解析。"""
    if not raw:
        return None
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
