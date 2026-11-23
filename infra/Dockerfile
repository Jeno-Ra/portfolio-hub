# ============================================================
# PortfolioHub 生产镜像（多阶段构建）
#
# 阶段 1 builder：只负责把依赖装进 /app/.venv
# 阶段 2 runtime：只搬 venv + 代码，不带 pip/uv/编译工具
#
# 三个体积/速度决策，都踩过坑：
#   1. 多阶段：单阶段会把 uv、pip 缓存、pip 本体留在镜像里
#   2. COPY --chown，而不是 COPY 后 chown -R：
#      chown -R /app 会改写每个文件的元数据 → Docker 生成一个**完整的新层**，
#      venv 被存两遍（实测多出 614 MB）
#   3. Debian 源换清华镜像：官方源在国内 pull 包要 3 分钟
# ============================================================

# ---------- 阶段 1：builder ----------
FROM python:3.12-slim AS builder

# UV_COMPILE_BYTECODE=0：编译 .pyc 会让 venv 大 100 MB 左右，
# 换来的启动加速对这个项目可以忽略
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=0 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

RUN pip install -i https://pypi.tuna.tsinghua.edu.cn/simple uv

WORKDIR /app

# 先只复制依赖清单：依赖没变时这一层命中缓存，改代码不会重装依赖
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# ---------- 阶段 2：runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Debian 官方源在国内很慢（实测 3 分钟），换清华源
# libgomp1：chroma/onnx 运行时要的 OpenMP
# tzdata：日志时间戳要按 TZ=Asia/Shanghai 显示，镜像里默认没有时区库
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 tzdata \
    && rm -rf /var/lib/apt/lists/*

# 先建用户，后面用 COPY --chown 直接给权限（不要再 chown -R）
RUN useradd --create-home --uid 10001 appuser

WORKDIR /app

# /app/data 是命名卷的挂载点。必须在这里建好并交给 appuser：
#   卷首次挂载时，Docker 会沿用镜像里这个目录的属主；
#   如果镜像里没有这个目录，卷会被 root 拥有，appuser 写入就会 Permission denied。
RUN mkdir -p /app/data && chown appuser:appuser /app/data

COPY --chown=appuser:appuser --from=builder /app/.venv /app/.venv
COPY --chown=appuser:appuser backend ./backend
COPY --chown=appuser:appuser frontend ./frontend

USER appuser

EXPOSE 8000

# 镜像里没有 curl，用 Python 自检
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

# 必须绑 0.0.0.0：容器内的 127.0.0.1 只有容器自己能访问
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
