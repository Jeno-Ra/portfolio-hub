# PortfolioHub

把散落在各处的作品（视频 / 图片 / 文本 / 代码）汇总成一个可检索、可自然语言管理的多模态作品集。

## 技术栈

| 层级        | 选型                               |
| ----------- | ---------------------------------- |
| 语言 / 依赖 | Python 3.12 + uv                   |
| 后端        | FastAPI + Pydantic v2              |
| 数据库      | SQLite + SQLAlchemy 2.x（ORM）     |
| 向量检索    | Chroma                             |
| 前端        | 原生 HTML + CSS + JS               |
| 质量        | pytest + Ruff + GitHub Actions     |
| 部署        | Docker + docker-compose + 云服务器 |

## 目录结构

| 目录             | 用途                                    |
| ---------------- | --------------------------------------- |
| `backend/app/`   | 后端应用（路由 / 模型 / 数据层 / 服务） |
| `backend/tests/` | 测试用例                                |
| `frontend/`      | 原生单页前端                            |
| `infra/`         | 部署相关                                |

## 接口

| 方法 | 路径                | 说明                              | 成功状态码 |
| ---- | ------------------- | --------------------------------- | ---------- |
| GET  | `/health`           | 健康检查                          | 200        |
| POST | `/works`            | 新增作品                          | 201        |
| GET  | `/works`            | 作品列表，支持 `skip` / `limit` 分页 | 200        |
| GET  | `/works/{work_id}`  | 查询单个作品                      | 200        |
| POST | `/ask`              | 提问：写入会话与消息，调用大模型返回答案 | 200        |

`/works` 返回的作品字段：`id` / `title` / `type` / `content` / `tags` / `created_at`。
`tags` 传名字即可，服务端会自动建标签并在 `work_tags` 里建立关联。
时间字段统一为 ISO 8601 的 UTC 形式（如 `2026-10-29T10:36:53Z`，带 `Z`），时区转换由客户端负责。

## 环境变量

复制模板并填入密钥：

```bash
cp .env.example .env
```

| 变量                 | 必填 | 默认值                    | 说明                                              |
| -------------------- | ---- | ------------------------- | ------------------------------------------------- |
| `DEEPSEEK_API_KEY`   | ✅   | —                         | 从 platform.deepseek.com 获取                     |
| `DEEPSEEK_BASE_URL`  |      | `https://api.deepseek.com` | 兼容其他 OpenAI 协议服务                          |
| `DEEPSEEK_MODEL`     |      | `deepseek-chat`           | 模型名                                            |
| `LLM_TIMEOUT`        |      | `5`                       | 单次请求超时（秒），过小会误杀正常回答            |
| `LLM_TOTAL_BUDGET`   |      | `30`                      | 整个重试链的总预算（秒），必须小于网关超时        |

> `.env` 已写入 `.gitignore`，不会进入版本库；`.env.example` 只含变量名，可安全提交。

## 快速开始

```bash
uv sync                                  # 按 uv.lock 安装依赖
cp .env.example .env                     # 填入 DEEPSEEK_API_KEY
uv run uvicorn backend.app.main:app --reload
```

启动后访问 `/docs` 可查看交互式接口文档（FastAPI 自动生成）。

## 大模型调用的容错

`/ask` 背后由 `backend/app/llm.py` 统一封装，具备三层保护：

| 机制 | 行为                                                                                     |
| ---- | ---------------------------------------------------------------------------------------- |
| 超时 | 单次请求 `LLM_TIMEOUT`（默认 5 秒），超时视为本次失败                                     |
| 重试 | 仅对超时 / 连接失败 / 限流重试，最多 4 次，指数退避（0.5→1→2→4 秒）；认证与参数错误不重试 |
| 降级 | 超出预算或重试耗尽时返回兜底文案，接口不返回 500                                          |

`LLM_TOTAL_BUDGET` 保证单次 `/ask` 的最坏耗时可控（默认 30 秒），不会因上游故障拖垮服务。
