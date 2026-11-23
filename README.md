# 作品集（PortfolioHub）

[![CI](https://github.com/Jeno-Ra/portfolio-hub/actions/workflows/ci.yml/badge.svg)](https://github.com/Jeno-Ra/portfolio-hub/actions/workflows/ci.yml)

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
| 部署        | Docker 多阶段构建 + docker-compose + 自托管（局域网 / Tailscale） |

## 目录结构

| 目录             | 用途                                          |
| ---------------- | --------------------------------------------- |
| `backend/app/`   | 路由 / 模型 / 数据层 / 检索 / 意图路由 / 权限策略 |
| `backend/tests/` | 测试用例                                      |
| `backend/media/` | 网页上传的文件副本（本地跑时的默认落点，不入库） |
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
| POST   | `/works/upload`     | **网页上传**：multipart 直接传文件，后端落盘后解析（推荐） |
| POST   | `/works/ingest`     | 从服务器上的路径录入（脚本 / `docker cp` 场景）    |
| POST   | `/ask`              | 提问：意图路由 → 检索 → 大模型，返回答案与引用     |
| POST   | `/ask/stream`       | 同上，SSE 流式返回（meta → delta → done）         |

`/works` 返回字段：`id` / `title` / `type` / `content` / `tags` / `source_path` / `created_at`。
`tags` 传名字即可，服务端会自动建标签并在 `work_tags` 里建立关联。

## 怎么加作品：拖进来就行

网页里点「+ 新增」后，可以：

- **把文件拖到虚线框里**（一次拖多个 = 一次建多条作品），或点框选文件；
- **把文件直接拖到窗口任意位置**——弹窗会自动打开并带上这个文件；
- 有文件时「标题」可以留空（自动取文件名）、「内容」变成「备注（可选）」，
  备注会拼在解析出来的正文最前面。

解析规则：图片交给视觉模型写描述，PDF / Word / PPT / Excel / 代码 / 纯文本抽取正文，
视频只存元数据（可再传一张封面图，会补一句画面描述）。

为什么不是「填一个本地文件路径」：浏览器出于安全拿不到 `<input type=file>` 的真实路径，
所以网页只能把**文件本体**上传上来，后端存一份副本再解析。副本落在 `media/`
（容器里是 `/app/data/media`），单个文件上限 512 MB。

删除作品时只删这份副本；`source_path` 指向你磁盘上原始位置时**绝不删除原文件**。

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
| `MEDIA_DIR`         |      | `backend/media`            | 上传副本存放目录（容器里是 `/app/data/media`） |

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

### 方式二：Tailscale（出门/4G 也能用，推荐）

Tailscale 是一层自建虚拟组网：两台设备各装一个客户端、登录同一账号，就组成一个私有网络，
彼此用固定的 `100.x.x.x` 地址互访。**不开公网端口**，也不受校园网 AP 隔离影响。

实测本机配置：

| 设备 | Tailscale 地址 |
| ---- | -------------- |
| 电脑 `desktop-qp94kil` | `100.126.133.63` |
| 手机 `cet-al00` | `100.86.250.22` |

步骤：

```bash
# 1. 电脑端：安装并登录（Windows 版 msi）
winget install Tailscale.Tailscale   # 或从 tailscale.com/download 手动安装
tailscale up                          # 浏览器里授权登录

# 2. 手机端：装 Tailscale（应用商店/官网 APK），登录同一个账号

# 3. 电脑上查自己的地址
tailscale status
# 100.126.133.63  desktop-qp94kil  Jeno-Ra@  windows

# 4. 手机浏览器 / App 里填这个地址
# http://100.126.133.63:8000
```

验证是不是真的通了（在电脑上跑）：

```bash
tailscale ping 100.86.250.22
# 前两次可能显示 via DERP(sfo) —— 走中继，慢
# 第 3 次变成 via 183.9.225.214:36418 —— 打洞成功，P2P 直连
```

实测延迟（`curl -N POST /ask/stream`）：

| 路径 | 首字节 | 总耗时 |
| ---- | ------ | ------ |
| 本机 localhost | 115 ms | 133 ms |
| 经 Tailscale 直连 | 292 ms | 320 ms |

隧道额外开销约 180 ms，流式输出不受影响（`meta → delta → done` 照常逐字到达）。

超过 3 台设备/团队用要付费，个人设备（手机+电脑）免费额度足够。

### 注意

服务跑在电脑上，所以**电脑关机会连不上**。常见做法：

- 需要时开机（最简单）
- 插电时把电源计划设为不休眠，Docker Desktop 设成开机自启
- 想 7×24 在线的话，把服务放到常开设备（旧手机/迷你主机/NAS）

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

## 桌面客户端（Windows）

不想每次开浏览器敲网址，就在桌面放一个「作品集」图标：双击 → 独立窗口打开，没有地址栏、没有标签页。

### 安装一次

```powershell
powershell -ExecutionPolicy Bypass -File desktop\install-shortcut.ps1
```

会在**桌面**和**开始菜单**各建一个快捷方式（换电脑或重装系统后重跑一次）。

### 双击之后发生了什么

`desktop/启动作品集.ps1` 做三件事：

1. 请求 `http://127.0.0.1:8000/health`
2. 没起来就拉起 Docker Desktop → `docker compose up -d` → 轮询到健康（期间显示一个居中的等待小窗）
3. 用 Edge 的 `--app` 模式开窗：`msedge.exe --app=http://127.0.0.1:8000/ --window-size=1320,880`

实测冷启动（容器全停 → 双击 → 页面可用）：**14.8 秒**，其中 Docker 引擎本来就在跑的话只花容器启动那几秒。

### 为什么不用 Electron / Tauri

| 方案 | 体积 | 成本 |
| ---- | ---- | ---- |
| Edge `--app` | 0（系统自带 Edge） | 一个 40 行脚本 |
| Electron | +150 MB 运行时 | 要维护 package.json、打包、签名 |
| Tauri | +几 MB（用系统 WebView2） | 要装 Rust 工具链，编译链更长 |

三者显示出来的都是同一个网页，区别只在“谁来渲染”和“要不要分发安装包”。
自己一个人用、机器上本来就有 Edge，`--app` 是性价比最高的；真要做成能发给别人的 `.exe` 安装包，再上 Tauri。

### 图标

`desktop/app.ico` 由脚本生成（蓝色圆角方块 + 白色「作」，和前端主题色一致）：

```bash
python desktop/make_icon.py     # 需要 Pillow，输出 16/32/48/64/128/256 六个尺寸
```

### 局限

- 用的是 Edge 的内核，所以 Edge 被卸载/改名就没法用（Windows 10/11 默认自带）
- 窗口里没有浏览器菜单，前进后退快捷键仍然可用；要 DevTools 用 `F12`
- 服务仍需电脑开着：手机端（APK/浏览器）通过 Tailscale 连的也是这台机器

## Android APP（Capacitor 外壳）

`mobile/` 用 Capacitor 把页面装进安卓 App。**它不是独立后端**：数据仍然连你电脑上的服务。

> **界面是远程加载的**：`capacitor.config.json` 里配了 `server.url`，App 启动时直接从后端拉页面。
> 所以改前端（界面、交互）**不用重新装 APK**——电脑上 `docker compose up -d --build` 重建一次，
> 手机退到桌面再打开，就是新界面。只有改原生层（包名、图标、权限、App 名字）才需要重编重装。

### 编译

```bash
cd mobile
npm install            # 首次
npm run build:apk      # 一条命令：生成 www/ 兜底页 → cap sync → gradle 编译
# 产物：app/build/outputs/apk/debug/app-debug.apk
```

### 前置环境

| 需要 | 版本 | 说明 |
| ---- | ---- | ---- |
| JDK | **21** | Capacitor 8 要求；JDK 17 会报「无效的源发行版: 21」 |
| Android SDK | platform 35+ / build-tools 35+ | `sdkmanager` 安装 |
| Gradle | 8.x（wrapper 自带） | 国内换镜像 `mirrors.cloud.tencent.com/gradle/` |

本机实际配置：

```
JAVA_HOME        F:\Android\jdk21\jdk-21.0.13+9
ANDROID_HOME     F:\Android\sdk
GRADLE_USER_HOME F:\Android\gradle
```

### 后端地址与「不用重装」模式

地址只写在 `mobile/capacitor.config.json` 的 `server.url`（默认 `http://100.126.133.63:8000`，本机的 Tailscale 地址）：

```json
"server": {
  "url": "http://100.126.133.63:8000",
  "cleartext": true,
  "errorPath": "error.html"
}
```

- 想换地址（比如改用局域网）只改这一行，再 `npm run build:apk` 一次；
- `errorPath` 指向 `www/error.html` 兜底页：连不上时会告诉你检查 Tailscale / Docker，而不是白屏；
- 远程加载的副作用是**必须先连上后端**才能看到界面（这本来也是唯一的用法），
  真要离线看，把 `server` 整段删掉重编即可退回「界面打包进 APK」的老模式。

APK 需要允许明文 HTTP（`android:usesCleartextTraffic="true"`，已配），否则 Android 9+ 会拦掉 `http://` 请求。

### App 名字与图标文字

显示名不在 `capacitor.config.json` 里生效，而是 Android 资源：

```
mobile/android/app/src/main/res/values/strings.xml
  <string name="app_name">作品集</string>
  <string name="title_activity_main">作品集</string>
```

改完必须重新 `npm run build:apk`（`cap sync` 不会改这个文件）。
界面里的标题在 `frontend/index.html`（`<title>` 与 `.topbar__title`），两处都改了才是统一的名字。

### 安装到手机

1. 把 `mobile/dist/作品集-手机安装.apk`（桌面上的同名文件）传到手机（数据线 / 微信文件传输）
2. 点击安装，允许「未知来源」；装了旧版会提示覆盖
3. 手机开着 Tailscale、电脑上 `docker compose up -d` 在跑，就能用——**不需要同一个 WiFi**

打开后是全屏界面（无浏览器地址栏），功能与网页版一致。
**装完这一版之后，日常改界面（按钮、排版、新功能）都不再需要重装 APK。**

## 测试

```powershell
uv run pytest        # 24 个用例，实测 2.8 秒
uv run ruff check .  # 静态检查
```

两条硬规则决定了测试怎么写：

| 规则         | 做法                                                                                        | 为什么                                         |
| ------------ | ------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| 不联网       | `backend/tests/fakes.py` 把 embedding / 对话 / 意图解析 / 向量库全换成假实现                 | 真调一次要花钱、CI 上会超时，还测不稳           |
| 不碰真实数据 | `conftest.py` 在导入应用之前把 `DB_PATH` / `MEDIA_DIR` / `CHROMA_DIR` 指到临时目录，每个用例重建表 | 测试绝不能写进你自己的作品库                   |

**不需要 `.env`**：三个 API key 在测试里用占位值，所以新克隆的仓库（或 CI）直接 `uv run pytest` 就能跑。

`test_runs_on_temporary_database` 是保护性断言：万一环境变量没生效，它会先失败报警，而不是让后面的用例把真库清空。

覆盖范围：

| 文件          | 覆盖                                                                                                  |
| ------------- | ----------------------------------------------------------------------------------------------------- |
| `test_chunking.py` | 切片长度、重叠、边界不被切断、超长无标点不死循环、拼回原文逐字相等、句末切点（8 条）          |
| `test_api.py`      | 健康检查、作品增删改查与 404、AI 只能改标题不能改内容、删除二次确认、网页上传落盘/入库/删副本清理、检索未命中不调模型、命中时上下文含目录与片段、流式事件顺序 `meta → delta×N → done`（10 条） |
| `test_retrieval.py` | 距离阈值过滤（0.45 闭区间）、top-k 与问题向量传参、命中全被过滤时回退拒绝、同一作品多片段去重、引用编号与 sources 同序、上下文含目录+片段（6 条） |

## 已知取舍

| 取舍 | 原因 |
| ---- | ---- |
| 视频只存元数据，不做内容级检索 | 抽帧 + 语音转写要额外 40 小时，总预算只有 160 小时 |
| 切片 500 字 + 50 字重叠 | 重叠是为了避免语义被切断在边界 |
| 距离阈值 0.45 是实测校准值 | 相关 0.320–0.384，无关 0.510–0.654；作品量变大后需重新校准 |
| 「刚才那个」指代用进程内变量 | 单用户原型够用；多用户要改成按会话存储 |
| App 界面远程加载 | 换来「改前端不用重装 APK」；代价是没连上后端时只有兜底页 |
