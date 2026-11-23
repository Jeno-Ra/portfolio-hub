"""故意留一个没用到的导入，用来验证 CI 会拦住这种提交。

CI 跑起来之后这个文件会被删掉——它只是"红"的那一半证据。
"""

import os  # 故意不写 noqa：这一行会让 ruff 报 F401，用来验证 CI 会红


def demo() -> str:
    return "ci red demo"
