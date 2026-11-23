"""应用入口：只负责组装——日志、中间件、路由挂载、前端静态托管。

业务都在各自的模块里：
- `projects_api` 项目（容器）的增删改查、挂素材 / 摘素材
- `assets_api`   素材的增删改查与两种录入方式
- `qa_api`       提问（检索回答 / 统计 / 改名删除）
- `settings_api` 密钥设置、探活、退出

之前这些全堆在这一个文件里（750 行），改任何一块都要先在一屏之外找位置；
拆开的副作用是 import 变多了，好处是每个文件只讲一件事。
"""

import logging
import sys
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app import (
    assets_api,
    logging_setup,
    projects_api,
    qa_api,
    response,
    settings_api,
    user_config,
)
from backend.app.config import IS_FROZEN
from backend.app.database import init_db
from backend.app.response import Envelope, ok
from backend.app.uploads import MEDIA_DIR

logging_setup.configure()

app = FastAPI(title="PortfolioHub API", version="0.1.0")

# 全局异常 → 统一信封（4xx/5xx 也长成 {code,message,data}）
response.install(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发期放开；部署后收紧成具体前端域名
    allow_credentials=False,  # 与 allow_origins=["*"] 不能同时为 True
    allow_methods=["*"],
    allow_headers=["*"],
)

access_log = logging.getLogger("portfolio.access")

# 静态资源不打日志：一次页面加载就三行，会把真正的接口请求埋掉
QUIET_PATHS = ("/style.css", "/app.js", "/favicon.ico")


@app.middleware("http")
async def request_context(request: Request, call_next):
    """给每个请求一个请求号，并记录「一行一条」的结构化访问日志。

    请求号同时进响应头 `X-Request-ID`：用户截个图就能把「哪一次」说清楚，
    500 的响应体里也会带上它，和日志里的同一行对得上。
    """
    request_id = uuid.uuid4().hex[:8]
    request.state.request_id = request_id
    started = time.perf_counter()

    reply = await call_next(request)

    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    reply.headers["X-Request-ID"] = request_id
    if request.url.path not in QUIET_PATHS:
        # 流式接口的 ms 只算到"开始往外推"为止，不是生成完成的时间
        access_log.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": reply.status_code,
                "ms": elapsed_ms,
            },
        )
    return reply


@app.middleware("http")
async def fresh_frontend(request: Request, call_next):
    """HTML 每次都回源校验。

    前端是 StaticFiles 直接吐出去的，浏览器会把 index.html 也缓存住；
    改完界面再点「新增」还是旧 JS，表现就是"点了没反应"。这里让 HTML 每次
    都重新验证，静态资源则靠 index.html 里的 ?v= 版本号打破缓存。
    """
    reply = await call_next(request)
    if reply.headers.get("content-type", "").startswith("text/html"):
        reply.headers["Cache-Control"] = "no-cache, must-revalidate"
    return reply


# 启动时确保表存在。create_all 是幂等的：缺哪张建哪张，不动已有数据
init_db()

# 打包版没有 .env：密钥存在用户目录的 设置.json 里，启动时灌进 settings
user_config.apply()


@app.get("/health", response_model=Envelope[dict])
def health():
    return ok({"status": "ok", "version": app.version})


app.include_router(projects_api.router)
app.include_router(assets_api.router)
app.include_router(qa_api.router)
app.include_router(settings_api.router)


# 上传的副本要能直接访问：项目封面、以后的素材预览都靠它。
# 只暴露 media/ 这一层，用户原始文件不在这个目录里，所以不会被顺带暴露出去。
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")


# 前端静态托管。必须放在所有 API 路由之后：
# FastAPI 先匹配已注册的路由，匹配不到才落到这个 mount，
# 所以 /projects、/assets、/ask、/docs 不会被它吃掉。
#
# 为什么同源托管：手机上访问时，"127.0.0.1" 指的是手机自己而不是电脑。
# 前后端同源后前端用相对路径请求，一个地址（如 http://<电脑IP>:8000）就够，
# 也不用再配 CORS。
if IS_FROZEN:
    # PyInstaller 把 frontend 解到临时目录（sys._MEIPASS），不再是仓库相对位置
    FRONTEND_DIR = Path(getattr(sys, "_MEIPASS", ".")) / "frontend"
else:
    FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
