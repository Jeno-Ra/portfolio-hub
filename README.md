# PortfolioHub

把散落在各处的作品（视频 / 图片 / 文本 / 代码）汇总成一个可检索、可自然语言管理的多模态作品集。

## 技术栈

| 层级        | 选型                               |
| ----------- | ---------------------------------- |
| 语言 / 依赖 | Python 3.12 + uv                   |
| 后端        | FastAPI + Pydantic v2              |
| 数据库      | SQLite                             |
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
| POST | `/ask`              | 提问，当前返回固定假答案          | 200        |

启动服务：`uv run uvicorn backend.app.main:app --reload`

启动后访问 `/docs` 可查看交互式接口文档（FastAPI 自动生成）。
