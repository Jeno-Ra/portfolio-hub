"""文本切片：把长文本切成带重叠的片段，供向量化使用。

设计要点（面试常问，先在这里写清，避免以后只剩代码没有理由）：

1. **大小 500 字**：主流中文 embedding 模型输入上限 512 token，一个汉字约 1 token，
   取 500 留出余量，避免被模型静默截断。
2. **重叠 50 字（约 10%）**：避免语义正好被切在边界上。例如
   「明长城／的分镜脚本」被劈成两段后，两段各自都不再指向完整语义。
3. **优先在句末切**：固定长度硬切会把句子拦腰截断，所以在窗口末尾 40% 范围内
   回溯到最近的句号/换行；找不到才硬切。
4. **纯函数**：只做「字符串 → 字符串列表」，不碰数据库、不调接口，
   这样第 8 周可以直接用 pytest 单测，不需要起服务。
"""

SENTENCE_MARKS = ("。", "！", "？", "；", "\n")


def split_text(text: str, size: int = 500, overlap: int = 50) -> list[str]:
    """把 text 切成若干片段，每段不超过 size 个字符，相邻片段重叠 overlap 个字符。

    返回的片段是原文的**连续切片**（不做 strip），因此
    ``chunks[i][-overlap:] == chunks[i + 1][:overlap]`` 恒成立——
    重叠是否真的生效，靠这条不变式断言，而不是靠肉眼。
    """
    text = (text or "").strip()
    if size <= 0:
        raise ValueError("size 必须为正数")
    if not 0 <= overlap < size:
        raise ValueError("overlap 必须满足 0 <= overlap < size")
    if not text:
        return []
    if len(text) <= size:
        return [text]

    chunks: list[str] = []
    start = 0
    total = len(text)

    while start < total:
        end = min(start + size, total)

        if end >= total:  # 最后一段，直接收尾
            chunks.append(text[start:])
            break

        window = text[start:end]
        cut = max(window.rfind(mark) for mark in SENTENCE_MARKS)
        if cut >= int(size * 0.6):  # 只在窗口后 40% 找断点，否则片段会太短
            end = start + cut + 1

        chunks.append(text[start:end])

        # 回退 overlap 形成重叠；必须保证前进至少 1 个字符，否则会死循环
        next_start = end - overlap
        start = next_start if next_start > start else end

    return chunks
