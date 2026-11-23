"""结构化日志：一行一个 JSON，`docker logs` 里能直接 grep、也能被日志系统解析。

不引第三方库的原因：我们只有两种行（请求行 / 应用行），几十行 formatter 就够，
少一个依赖就少一个装不上的可能（打包成 exe 时尤其敏感）。

请求行长这样（`ms` 是服务端处理耗时，流式接口只算到"开始往外推"为止）：

    {"ts":"2026-11-23T20:31:02+0800","level":"INFO","logger":"portfolio.access",
     "msg":"request","request_id":"9f2c1ab4","method":"POST","path":"/ask",
     "status":200,"ms":812.4}
"""

import json
import logging
import sys

# 这些字段来自 `extra=`，是结构化日志的正文；其余（msg/level/logger）是骨架
EXTRA_FIELDS = ("request_id", "method", "path", "status", "ms", "event")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for field in EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            # 堆栈只进日志。响应里只给请求号，不给调用方看内部结构
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure(level: int = logging.INFO) -> None:
    """接管根 logger。uvicorn 自己的 access log 会和我们重复，关掉。"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    # uvicorn 的 access log 是纯文本，且和我们中间件记录的是同一件事
    logging.getLogger("uvicorn.access").disabled = True
