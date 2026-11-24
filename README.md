# 作品集

一个面向个人创作者的本地作品管理工具：以**项目**组织内容，以**素材**保存图片、视频、音频、文本、代码和文档，并支持任意嵌套分组、预览、检索和多设备访问。

## 功能

- 项目 / 素材两级模型：一个项目可包含多种素材。
- 素材可跨项目复用，项目内支持任意嵌套分组。
- 树形分组导航：全部素材、待归类、子分组、类型筛选。
- 多选批量移动和拖拽归类。
- 素材可编辑，也可以替换源文件；替换后自动重新解析和建立索引。
- 图片、视频、音频、PDF、文本和代码预览。
- 基于 Chroma 的语义检索与 DeepSeek 问答，支持 SSE 流式输出。
- Docker 正式版、Windows 桌面客户端、手机端远程访问。

## 快速开始：Docker 正式版

### 前置条件

- Windows 10/11
- Docker Desktop（WSL2 后端）
- 已准备模型服务密钥

### 启动

1. 复制 `.env.example` 为 `.env`。
2. 在 `.env` 中填写 `DEEPSEEK_API_KEY`、`EMBEDDING_API_KEY` 和 `GPT_API_KEY`。
3. 在仓库根目录执行：

```powershell
docker compose up -d --build
```

4. 打开 <http://127.0.0.1/>。

检查服务：

```powershell
Invoke-RestMethod http://127.0.0.1/health
```

停止服务但保留数据：

```powershell
docker compose stop
```

> 不要使用 `docker compose down -v`，它会删除数据库和媒体卷。

## Windows 桌面客户端

桌面客户端是 Docker 正式版的窗口入口，不再创建第二套 SQLite 数据库。双击 `desktop/dist/作品集.exe` 后，它会：

1. 检查 Docker 引擎；
2. 必要时启动 Docker Desktop 和 compose；
3. 打开同一个 `http://127.0.0.1:8000/` 服务。

因此桌面客户端、浏览器和手机端看到的是同一套项目数据。

重新打包：

```powershell
powershell -ExecutionPolicy Bypass -File desktop\build.ps1
```

## 手机访问

手机端是 Capacitor 外壳，通过 Tailscale 访问电脑上的 Docker 服务。电脑和手机登录同一个 Tailscale 网络后，使用电脑的 Tailscale 地址访问 `8000` 端口。电脑关机或 Docker 停止时，手机端无法访问。

## 数据位置与安全

Docker 数据使用命名卷保存：

- PostgreSQL：项目、素材、分组、标签和会话
- Chroma：素材向量索引
- appdata：上传媒体文件

备份前先停止写入服务，再执行 `scripts/backup.ps1`。备份应同时保留数据库导出和媒体文件，并核对清单哈希。不要把 `.env`、数据库、上传文件或真实作品提交到 Git。

## 数据模型

```text
项目
├── 分组
│   └── 子分组（可无限嵌套）
└── 素材
    ├── 图片
    ├── 视频
    ├── 音频（保存元数据和手动描述）
    ├── 文本
    ├── 代码
    └── 文档
```

素材类型由文件扩展名判定，内置类型为 `image`、`video`、`audio`、`text`、`code`、`document`。删除项目默认只删除容器，不删除素材；只有明确指定 `with_assets=true` 才会连素材一起删除。

## API 概览

| 方法 | 路径 | 用途 |
|---|---|---|
| GET/POST | `/projects` | 项目列表 / 新建项目 |
| GET/PATCH/DELETE | `/projects/{id}` | 查看、修改、删除项目 |
| POST | `/projects/{id}/cover` | 上传项目封面 |
| GET/POST | `/projects/{id}/groups` | 分组列表 / 新建分组 |
| PATCH/DELETE | `/projects/{id}/groups/{group_id}` | 修改 / 删除分组 |
| PATCH | `/projects/{id}/assets/group` | 批量移动素材 |
| GET/POST | `/assets` | 素材列表 / 新建素材 |
| POST | `/assets/upload` | 上传素材 |
| POST | `/assets/{id}/file` | 替换素材源文件 |
| GET/PATCH/DELETE | `/assets/{id}` | 查看、修改、删除素材 |
| GET/HEAD | `/assets/{id}/preview` | 预览素材 |
| POST | `/ask` | 同步问答与管理操作 |
| POST | `/ask/stream` | SSE 流式问答 |
| GET/PUT/POST | `/settings`、`/settings/test` | 设置与连接测试 |

成功响应统一为：

```json
{"code": 0, "message": "ok", "data": {}}
```

流式问答使用 `meta`、`delta`、`done` 事件，不使用 JSON 信封包裹每个事件。

## 开发与测试

安装依赖：

```powershell
uv sync
```

运行测试和检查：

```powershell
uv run pytest -q
uv run ruff check .
node --check frontend/app.js
```

测试使用临时数据库和 mock 服务，不读取真实作品库，也不会调用模型 API。

## 目录

```text
backend/app/       FastAPI、数据模型、业务路由、检索和索引
backend/tests/     隔离测试
desktop/           Windows 桌面客户端与打包脚本
docs/              部署说明
frontend/          Web 界面
infra/             Dockerfile 与 Nginx 配置
mobile/            Android Capacitor 外壳
scripts/           部署和备份脚本
docker-compose.yml  Docker 正式版编排
```

## 许可与数据边界

本仓库不包含个人作品、上传文件、API 密钥或运行时数据库。使用者应自行配置密钥并管理自己的数据。
