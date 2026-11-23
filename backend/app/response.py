"""统一返回格式 + 全局异常处理。

所有 JSON 接口都返回 `{code, message, data}`：`code == 0` 才是成功，
其余是业务错误码（前端只认 code 做分支，状态码留给网关和日志看）。

两个刻意的例外，写在这里免得以后有人以为是漏了：
- `/ask/stream` 是 SSE 事件流，一帧一帧推，套信封就没有流的意义了；错误用 `event: error`；
- `/`、`/style.css` 这些是 StaticFiles 吐的 HTML/CSS，不是 JSON。

为什么保留 HTTP 状态码：`curl -i`、nginx 日志、容器探活、CI 里的断言都看状态码。
code 和 status 同时给出，两边都不别扭。
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from openai import (
    APIStatusError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app import errors

log = logging.getLogger("portfolio")

OK = 0

# 名字 -> (业务码, HTTP 状态)。业务码按段分：1xxx 调用方问题 / 2xxx 上游 / 3xxx 自己
ERRORS: dict[str, tuple[int, int]] = {
    "bad_request": (1001, 400),
    "not_configured": (1002, 400),
    "not_found": (1003, 404),
    "validation": (1005, 422),
    "method_not_allowed": (1006, 405),
    "upstream_auth": (2001, 502),
    "upstream_rate_limit": (2002, 429),
    "upstream_error": (2003, 502),
    "internal": (3001, 500),
}

class Envelope[T](BaseModel):
    """`response_model` 用：保证外层形状，同时继续校验 data 里的字段。"""

    code: int = OK
    message: str = "ok"
    data: T | None = None


def ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    return {"code": OK, "message": message, "data": data}


def error(name: str, message: str) -> JSONResponse:
    code, status = ERRORS[name]
    return JSONResponse(
        status_code=status, content={"code": code, "message": message, "data": None}
    )


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


def _validation_message(exc: RequestValidationError) -> str:
    """把 Pydantic 的错误列表压成一句人话：字段位置 + 原因。"""
    parts = []
    for item in exc.errors()[:3]:
        where = ".".join(str(part) for part in item.get("loc", ()) if part != "body")
        parts.append(f"{where}：{item.get('msg', '参数不合法')}" if where else item.get("msg", ""))
    return "参数错误：" + "；".join(part for part in parts if part)


def install(app: FastAPI) -> None:
    """注册四个异常处理器。顺序无关：Starlette 按异常类的 MRO 找最接近的那个。"""

    @app.exception_handler(errors.NotConfigured)
    async def not_configured(request: Request, exc: errors.NotConfigured):
        # 用户还没填密钥，属于"配置没做完"，不是服务器故障：400 + 能照着做的提示
        return error("not_configured", str(exc))

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, exc: RequestValidationError):
        return error("validation", _validation_message(exc))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        name = {
            400: "bad_request",
            404: "not_found",
            405: "method_not_allowed",
        }.get(exc.status_code, "bad_request" if exc.status_code < 500 else "internal")
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return error(name, detail)

    @app.exception_handler(APIStatusError)
    async def upstream(request: Request, exc: APIStatusError):
        """上游模型服务的错误翻译成合适的语义。

        注册在 APIStatusError 上即可覆盖 AuthenticationError / RateLimitError 等子类
        （Starlette 沿 MRO 查找）。
        """
        if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
            return error("upstream_auth", "上游模型服务鉴权失败，请检查密钥是否正确")
        if isinstance(exc, RateLimitError):
            return error("upstream_rate_limit", "上游模型服务限流，请稍后重试")
        return error("upstream_error", f"上游模型服务返回错误 {exc.status_code}")

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        # 兜底：任何没被上面接住的异常都变成 500 + 请求号，堆栈只进日志不进响应
        request_id = _request_id(request)
        log.error(
            "unhandled exception",
            exc_info=exc,
            extra={"request_id": request_id, "path": request.url.path},
        )
        return error("internal", f"服务器内部错误，请把请求号 {request_id} 提供给维护者")
