"""三个模型客户端共用的「懒加载 + 可重载」逻辑。

为什么单独抽出来：llm / embedding / vision 原来各写了一遍
`_client / _make_client / _get_client / reload`，一字不差，只有密钥字段、
base_url 和报错文案不同。三份复制粘贴的后果很具体——改一处忘两处，
就会出现「明明填了密钥还报未配置」这类问题。

为什么是懒加载：打包版第一次启动时可能一个密钥都没有，
这时如果模块一 import 就 `OpenAI(api_key="")`，进程直接起不来。
改成第一次真正调用才建连接，用户填完密钥后 `reload()` 重建。
"""

from openai import OpenAI

from backend.app.config import settings
from backend.app.errors import NotConfigured


class LazyClient:
    """按需创建 OpenAI 客户端；密钥缺失时抛 NotConfigured（由全局处理器转 400）。"""

    def __init__(
        self, key_field: str, base_url_field: str, timeout_field: str, missing: str
    ) -> None:
        self._key_field = key_field
        self._base_url_field = base_url_field
        self._timeout_field = timeout_field
        self._missing = missing
        self._client: OpenAI | None = None

    def get(self) -> OpenAI:
        if self._client is None:
            key = getattr(settings, self._key_field)
            if not key:
                raise NotConfigured(self._missing)
            self._client = OpenAI(
                api_key=key,
                base_url=getattr(settings, self._base_url_field),
                timeout=getattr(settings, self._timeout_field),
                # 关闭 SDK 内置重试：重试权统一由业务层控制，
                # 否则一次失败会变成 1+2+4 次请求，耗时和预算都对不上
                max_retries=0,
            )
        return self._client

    def reload(self) -> None:
        """密钥变更后调用，下次请求用新密钥重建客户端。"""
        self._client = None
