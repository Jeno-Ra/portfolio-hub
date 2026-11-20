# PortfolioHub

把散落在各处的作品（视频 / 图片 / 文本 / 代码）汇总成一个可检索、可自然语言管理的多模态作品集。

## 技术栈

| 层级        | 选型                                         |
| ----------- | -------------------------------------------- |
| 语言 / 依赖 | Python 3.12 + uv                              |
| 后端        | FastAPI + Pydantic v2                        |
| 关系库      | SQLite（开发默认）/ PostgreSQL（容器编排）    |
| ORM         | SQLAlchemy 2.x                                |
| 向量检索    | Chroma（嵌入式开发 / 独立服务编排）           |
| 前端        | 原生 HTML + CSS + JS（无框架）                |
| 质量        | pytest + Ruff + GitHub Actions                |
| 部署        | Docker 多阶段构建 + docker-compose + 云服务器 |

## 目录结构

| 目录             | 用途                                          |
| ---------------- | --------------------------------------------- |
| `backend/app/`   | 路由 / 模型 / 数据层 / 检索 / 意图路由 / 权限策略 |
| `backend/tests/` | 测试用例                                      |
| `frontend/`      | 原生单页前端                                  |
| `infra/`         | Dockerfile                                    |
| `eval/`          | 评测语料、50 条评测集与评测脚本                |
| `docker-compose.yml` | 三服务编排（backend + postgres + chroma） |

## 接口

| 方法   | 路径                | 说明                                              |
| ------ | ------------------- | ------------------------------------------------- |
| GET    | `/health`           | 健康检查                                          |
| POST   | `/works`            | 新增作品（写入后自动建索引）                      |
| GET    | `/works`            | 作品列表，支持 `skip` / `limit` 分页              |
| GET    | `/works/{id}`       | 查询单个作品                                      |
| PATCH  | `/works/{id}`       | 更新作品（内容变了自动重建索引）                  |
| DELETE | `/works/{id}`       | 删除作品（同时清掉向量与切片）                    |
| POST   | `/works/ingest`     | 从文件录入：图片/视频/PDF/docx/pptx/xlsx/text…    |
| POST   | `/ask`              | 提问：意图路由 → 检索 → 大模型，返回答案与引用     |
| POST   | `/ask/stream`       | 同上，SSE 流式返回（meta → delta → done）         |

`/works` 返回字段：`id` / `title` / `type` / `content` / `tags` / `source_path` / `created_at`。
`tags` 传名字即可，服务端会自动建标签并在 `work_tags` 里建立关联。

## 问答是怎么走通的

```
用户提问
  ├─ 关键词命中「改成/删掉/记一条/多少条」→ 意图路由（LLM 抽槽位）
  │     → create / update / delete / stats
  └─ 否则 → search
        ├─ 问题向量化（bge-m3, 1024 维）
        ├─ Chroma top-5，按距离阈值 0.45 过滤
        ├─ 没命中 → 代码拼拒绝文案，不调用模型（零 token）
        └─ 命中 → 目录 + 相关片段喂给 deepseek-chat → 带引用编号回答
```

**AI 的写权限是受限的**（`backend/app/policy.py`）：只能改标题 / 标签 / 类型，
**不能改作品内容**（内容来自原始文件，改掉等于丢数据），也从不写磁盘上的源文件；
删除必须二次确认。

## 环境变量

复制模板并填入密钥：

```bash
cp .env.example .env
```

| 变量                | 必填 | 默认值                     | 说明                                    |
| ------------------- | ---- | -------------------------- | --------------------------------------- |
| `DEEPSEEK_API_KEY`  | ✅   | —                          | platform.deepseek.com                   |
| `DEEPSEEK_BASE_URL` |      | `https://api.deepseek.com` | 兼容其他 OpenAI 协议服务                |
| `DEEPSEEK_MODEL`    |      | `deepseek-chat`            | 对话模型                                |
| `LLM_TIMEOUT`       |      | `5`                        | 单次请求超时（秒）                      |
| `LLM_TOTAL_BUDGET`  |      | `30`                       | 整个重试链总预算（秒）                   |
| `EMBEDDING_API_KEY` | ✅   | —                          | 硅基流动（SiliconFlow）                 |
| `EMBEDDING_BASE_URL`|      | `https://api.siliconflow.cn/v1` | OpenAI 兼容接口                    |
| `EMBEDDING_MODEL`   |      | `BAAI/bge-m3`              | 换模型必须同步改 `EMBEDDING_DIM` 并重建向量库 |
| `EMBEDDING_DIM`     |      | `1024`                     | 维度守卫：不符会在写库前报错            |
| `GPT_API_KEY`       | ✅   | —                          | 图片描述用的视觉模型                    |
| `GPT_BASE_URL`      |      | `https://api.openai.com/v1`| 可指向任意兼容中转                      |
| `GPT_MODEL`         |      | `gpt-6.1-terra`            | 视觉模型名                              |
| `POSTGRES_PASSWORD` |      | `portfolio_dev_password`   | 仅 docker compose 使用                  |

> `.env` 已写入 `.gitignore`，不会进入版本库；`.env.example` 只含变量名，可安全提交。

## 快速开始

### 方式一：本地跑（SQLite + 嵌入式向量库）

```bash
uv sync
cp .env.example .env          # 填入密钥
uv run uvicorn backend.app.main:app --reload
uv run pytest -q              # 6 项切片测试
uv run python eval/run_eval.py  # 评测：recall@5 / 拒绝准确率
```

访问 `/docs` 看交互式接口文档。

### 方式二：容器跑（三服务）

```bash
cp .env.example .env          # 填入密钥
docker compose up -d --build  # backend + postgres + chroma
docker compose ps             # 等 backend / postgres 显示 healthy
curl http://127.0.0.1:8000/health
```

数据落在命名卷 `portfolio_appdata` / `portfolio_pgdata` / `portfolio_chromadata`。
**停服务用 `docker compose stop` 或 `down`，不要用 `down -v`**（`-v` 会连数据一起删）。

#### 实测数据（本机 4 GB / 4 核 WSL，2026-11-20）

| 场景 | 到 backend healthy |
| ---- | ------------------ |
| `docker compose restart`（只重启容器） | **6.5 秒** |
| `down` + `up`（删容器，保数据） | **11.1 秒** |
| `down -v` + `up --build`（全删重建） | **12.8 秒** |

| 指标 | 数值 |
| ---- | ---- |
| 镜像体积 | **796 MB**（单阶段写法会到 1.72 GB） |
| 三服务常驻内存 | backend 143 MB + postgres 35 MB + chroma 11 MB ≈ **190 MB** |
| 数据卷 | 3 个命名卷；删 backend 容器不影响 PG 与向量 |

#### 怎么确认真的跑起来了（不只是 `docker ps` 有输出）

```bash
# 1. 三个容器健康
docker compose ps                     # backend / postgres 应显示 (healthy)

# 2. 后端连的是 PostgreSQL
docker exec portfolio-backend python -c \
  "from backend.app.database import engine; print(engine.dialect.name)"   # -> postgresql

# 3. 后端连的是 chroma 服务（不是嵌入式）
docker exec portfolio-backend python -c \
  "from backend.app import vector_store as v; print(type(v._client._server).__module__)"
# -> chromadb.api.fastapi

# 4. 表建好了
docker exec portfolio-postgres psql -U portfolio -d portfolio -c '\dt'   # 6 张表

# 5. 端到端：建一条作品再问它
curl -s -X POST http://127.0.0.1:8000/works -H 'Content-Type: application/json' \
  -d '{"title":"测试","type":"text","content":"金山岭长城清晨五点拍摄，航拍 6 个镜头"}'
curl -s -X POST http://127.0.0.1:8000/ask -H 'Content-Type: application/json' \
  -d '{"question":"长城是什么时候拍的"}'      # 回答里应带 [1] 引用
```

#### 常见问题

| 现象 | 原因 | 处理 |
| ---- | ---- | ---- |
| `docker pull` 超时 / `dial tcp registry-1.docker.io` | Docker Hub 国内不可达 | 在 Docker Desktop → Settings → Docker Engine 里加 `registry-mirrors`（实测可用：`docker.m.daocloud.io`、`docker.1panel.live`、`hub.rat.dev`） |
| 引擎无响应、`docker info` 卡几十秒 | 同时跑了太多容器，4 GB 不够 | `wsl --shutdown` 重启引擎；一次只开一个项目 |
| `PermissionError: /app/data/chroma` | 命名卷属主是 root，容器内 appuser 写不进 | 镜像里预建目录并 `chown`（已在 Dockerfile 处理） |
| `container is unhealthy`（chroma） | 官方镜像是最小化镜像，没有 curl/python，写不了探活 | 改用代码里的 heartbeat 重试（`vector_store._build_client`） |
| `syntax error at or near "PRAGMA"` | `_ensure_columns()` 是 SQLite 方言 | 只在 SQLite 下执行（已加判断） |
| `/health` 通了但容器其实在崩 | 宿主机还有别的进程占着 8000 | `Get-NetTCPConnection -LocalPort 8000` 先确认端口没被占 |

## 多设备访问（手机 + 电脑共用一份库）

后端**同源托管前端**，所以只有一个地址，不需要另起静态服务器：

```
http://<电脑地址>:8000      # 页面 + API 都在这个端口
```

### 方式一：局域网（同一 WiFi，最简单）

```bash
# 查电脑内网 IP（Windows）
ipconfig | findstr IPv4
# 手机浏览器打开
# http://192.168.x.x:8000
```

需要放行 Windows 防火墙 8000 入站。部分校园网/公司网络开了 **AP 隔离**（设备之间互不可见），这种情况用方式二。

### 方式二：Tailscale（出门/4G 也能用）

1. 电脑和手机都安装 Tailscale，登录**同一个账号**
2. 手机浏览器访问 `http://<电脑的 Tailscale 设备名>:8000`

设备间走加密隧道，不暴露公网端口，也不受 AP 隔离影响。

### 注意

服务跑在电脑上，所以**电脑关机会连不上**。常见做法：

- 需要时开机（最简单）
- 插电时把电源计划设为不休眠，Docker Desktop 设成开机自启
- 想 7×24 在线的话，把服务放到常开设备（旧手机/迷你主机/NAS）



`/ask` 背后由 `backend/app/llm.py` 统一封装，三层保护：

| 机制 | 行为                                                                                       |
| ---- | ------------------------------------------------------------------------------------------ |
| 超时 | 单次请求 `LLM_TIMEOUT`（默认 5 秒），超时视为本次失败                                       |
| 重试 | 仅对超时 / 连接失败 / 限流重试，最多 4 次，指数退避（0.5→1→2→4 秒）；认证与参数错误不重试   |
| 降级 | 超出预算或重试耗尽时返回兜底文案，接口不返回 500                                            |

`LLM_TOTAL_BUDGET` 保证单次 `/ask` 最坏耗时可控（默认 30 秒）。

流式端点 `/ask/stream` **不重试**：流一旦开始输出，重试会让前端看到重复内容，
所以失败时发一个 `error` 事件，让前端把气泡替换成明确的错误提示。

## 已知取舍

| 取舍 | 原因 |
| ---- | ---- |
| 视频只存元数据，不做内容级检索 | 抽帧 + 语音转写要额外 40 小时，总预算只有 160 小时 |
| 切片 500 字 + 50 字重叠 | 重叠是为了避免语义被切断在边界 |
| 距离阈值 0.45 是实测校准值 | 相关 0.320–0.384，无关 0.510–0.654；作品量变大后需重新校准 |
| 「刚才那个」指代用进程内变量 | 单用户原型够用；多用户要改成按会话存储 |
