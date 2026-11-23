"""切片函数的单元测试。

运行：uv run pytest backend/tests/test_chunking.py -q
"""

import itertools

import pytest

from backend.app.chunking import split_text

LONG = (
    "明长城拍摄那天的光线非常好，云层压得很低，整段素材几乎没有废片。"
    "后期调色分三步：先统一白平衡，再压暗高光，最后加一点暖色。"
    "发布平台选了三个：视频号、B站、小红书，其中 B 站的数据最好。\n"
) * 30


def test_empty_returns_empty_list():
    assert split_text("") == []
    assert split_text("   \n  ") == []


def test_short_text_kept_whole():
    text = "这是一段很短的文字。"
    assert split_text(text) == [text]


def test_chunks_never_exceed_size():
    chunks = split_text(LONG, size=500, overlap=50)
    assert len(chunks) >= 4
    assert all(len(c) <= 500 for c in chunks)


def test_adjacent_chunks_overlap_by_exactly_overlap():
    chunks = split_text(LONG, size=500, overlap=50)
    for i in range(len(chunks) - 1):
        if len(chunks[i]) >= 50:
            assert chunks[i][-50:] == chunks[i + 1][:50]


def test_text_without_punctuation_terminates():
    """最坏情况：找不到断点必须硬切，且不能死循环。"""
    chunks = split_text("a" * 2000, size=500, overlap=50)
    assert len(chunks) >= 4
    assert all(len(c) <= 500 for c in chunks)


def test_invalid_overlap_rejected():
    with pytest.raises(ValueError):
        split_text("x" * 100, size=50, overlap=50)


def test_chunks_rebuild_the_original_text():
    """切片是原文的连续切片：按重叠拼回去必须逐字等于原文，不能丢字也不能重复。"""
    chunks = split_text(LONG, size=500, overlap=50)
    rebuilt = chunks[0]
    for previous, current in itertools.pairwise(chunks):
        shared = min(50, len(previous), len(current))
        assert previous[-shared:] == current[:shared]
        rebuilt += current[shared:]
    assert rebuilt == LONG.strip()


def test_cuts_at_sentence_end_when_available():
    """窗口后 40% 内有句号就以句号结尾，不把句子拦腰截断。"""
    text = "甲" * 400 + "。" + "乙" * 300
    chunks = split_text(text, size=500, overlap=50)
    assert chunks[0].endswith("。")
    assert len(chunks[0]) <= 500
