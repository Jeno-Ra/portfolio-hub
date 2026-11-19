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

## 大模型调用的容错

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
