"""项目自定义异常。

单独一个文件是为了避免循环导入：main.py 要捕获它，llm/embedding/vision 要抛它，
谁都不该 import main。
"""


class NotConfigured(RuntimeError):
    """缺少密钥或服务未配置。main.py 统一转成 400，提示用户去「设置」里填。"""
