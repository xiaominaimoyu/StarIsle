# 星屿 StarIsle

个人全栈门户：公开展示站点（星）与受 Cookie 保护的私有空间（屿）。前端使用 Astro，私有 API 使用 FastAPI；浏览器只请求同源 `/api/v1`，JWT 只保存在 Secure/HttpOnly Cookie 中。

本仓库的四份规划文档仍是计划基线；本 README 说明本机开发、验证、恢复和公开站 Pages 部署。完整私有栈与外部服务仍按各自验收证据判断。

## 当前进度（2026-11-04）

用户已确认交付范围为“公开站上线＋私有功能本地可用”。当前先读 [PROGRESS.md](./PROGRESS.md) 与 [开发进度](./docs/开发进度.md) 的 §65（公开站视觉）和 §64（后端修复）；邮件和相册恢复见 [恢复报告](./docs/2026-11-04_邮件与相册失败恢复.md) 与 §63。本轮基于 `4c1efb5` 完善移动邮件返回、刷新/分页失败恢复、独立已读请求及相册上传/删除刷新；Web 30 files / 204 项、Astro 114 files 零诊断、harness 31 项通过，完整隔离浏览器 12 主组/0 失败及新增 13 项前端恢复检查通过。本地模式临时构建 11 页成功，公开 55 文件逐项哈希不变。合成场景及失败尝试边界见本轮报告，源码尚未提交或推送。

上一批公开候选已于北京时间 11-04 08:36 发布为 `1cb49d51`，Cloudflare 当时确认 production success；线上 37 项首轮通过、1 项修正验收文本后补验通过，包含 19 项 HTTP、5 页 HTML 对应及桌面/移动/无脚本交互。该批 Caddy refresh、上传拒绝与 Linux 独立恢复/恢复根启用已通过，详见 [交付报告](./docs/2026-11-03_网站交付与本地恢复验收.md)。Docker 宿主 socket 故障和实际回滚未验收仍保留，本批不处理宿主环境；操作步骤见 [本地运行与恢复指南](./docs/本地运行与恢复指南.md)。该批 23 文件已按确认归档为 `4c1efb5`，本批私有界面改动不重新发布 Pages。

开工前复核见 [项目现状与恢复开发清单](./docs/2026-11-01_项目现状与恢复开发清单.md)，剩余依赖、验收与容量见 [后续开发计划](./docs/2026-11-01_后续开发计划.md)。四份原规划文档及下方 11-01 记录保留其历史证据。

以下三段为 11-01 至 11-03 前轮历史，数量和“未提交/未部署”等状态只对应当时批次；本轮最新结果以上文和 §63 为准。当前源码已具备公开门户、书桌信封入口、私有控制台、文件/照片、邮件接收、知识库/RAG 及运维脚本。11-01 首批修正公开内容和入口可访问性，随后发布 Pages 并完成当时移动 Lighthouse；仓库来源筛选器误匹配列表项的问题已修复。当时 Web 156、harness 安全 31 项、Astro 105 files 零诊断、静态构建 11 页及隔离浏览器 12 组/0 失败。真实收信、真实模型和私有云仍未验收，不属于本次确认的完成前提；原 M1/M2/M3 生产验收保留各自证据边界。

用户逐文件确认后，21 个文件已提交推送为 561ef4e，随后 18 个文件也已提交推送为 `main@c1cbe73`。11-03 开始时本地与实际远端一致，本轮网页修改尚未提交。姓名/教育背景、6 个 GitHub 仓库快照、项目源码链接和公开邮箱已落实，平台子域名已可用。首发保持音乐隐藏，授权曲目验收后置。

11-01 Docker 引擎恢复为 29.8.2，四种组合 config 预检 89 项通过；独立 API/worker/Caddy 栈的同源登录、上传入库、降级 SSE、CSRF 拒绝与登出通过。另起隔离栈的真实浏览器登录/退出也通过；两次首条 data 分别为 270.61ms / 458.18ms，临时应用栈清理。当时保留引擎运行；11-03 检查引擎未运行，本轮未启动 Docker，不冒充重跑容器验收，见 [基础联调](./docs/2026-11-01_Docker-Caddy隔离联调.md)。

随后以受控慢速上游完成真实 Caddy/Chromium 长流、取消/导航与 partial 错误验证，13 组检查通过；正常约 10 秒心跳可观察，4 种关闭场景均未保存未完成的 assistant。修复了断流运行告警带请求头的问题，默认 logger 过滤 request.headers，告警仍保留；新增 [可复用验收入口](./scripts/caddy_sse_e2e.py)，结果及失败记录见 [长流与日志脱敏](./docs/2026-11-01_Caddy长流与日志脱敏.md)。本批未改 RAG 产品逻辑，未重新发布 Pages，仍待主动刷新、文件/照片与容器恢复等验证。

## Cloudflare 公开站

公开站已发布至 [starisle-6bt.pages.dev](https://starisle-6bt.pages.dev/)，仅托管公开静态页面。私人入口显示暂未开放，控制台路径转至该提示页；API、SQLite、文件、邮件和 RAG 服务继续在本地开发。现有本地登录和控制台功能保留。

生产构建使用 `PUBLIC_SITE_URL=https://starisle-6bt.pages.dev` 和 `pnpm build:pages`，上传目录为 `apps/web/dist`。脚本关闭公开候选的登录表单，生成 Pages 重定向及响应头，并运行原发布门禁。当前生产部署为 `1cb49d51-d92d-4a07-930c-dae197b0be57`，北京时间 2026-11-04 08:36 发布成功；11 页构建与完整门禁通过，55 文件 fingerprint 为 `aec063eeb7b2dfcaf34045001986d39b57b6439f268810cf15e072bebd106e40`。部署基于未提交工作区，`commit_dirty=true`；线上复验结果以本轮交付报告最终记录为准。

11-01 旧部署的移动 Lighthouse 四项（性能/无障碍/最佳实践/SEO）为：开封后主页 96/100/100/100、项目和简历均 100/100/100/100；首次 `/` 实际进入 noindex 信封页，为 98/100/100/66，保留重定向警告，不能混为首页首访 SEO 已通过。这些是历史实验室样本，本轮未重新采集；原始报告和边界见 [Lighthouse 与筛选修复](./docs/2026-11-01_公开站Lighthouse与筛选修复.md)。发布、更新和回滚见 [Cloudflare Pages 部署](./docs/Cloudflare-Pages部署.md)。

## 本机私人书桌

公开站仍然只由 `pnpm build:pages` 生成。私人书桌不进 Astro 页面，而由本机 API 在 `http://127.0.0.1:8787` 直接提供。这样 `apps/web/dist` 里不会出现 `/private` 路由、私人数据或新的 API 地址。登录仍用现有的 HttpOnly Cookie 和 CSRF，相册仍走现有的 `/api/v1/photos`。没有注册接口。

数据默认在 `D:\StarIsleData`，备份默认在 `D:\ASUS\OneDrive\StarIsleBackup`。数据库用 SQLite 在线备份写成快照，保留最近 7 份；照片按内容增量复制。正在使用的数据库不会被复制进 OneDrive，也不会在 OneDrive 里打开。端口固定避开 8000 和 8001，留给以后的本机模型。

主人电脑上的命令见 [本地运行与恢复指南](./docs/本地运行与恢复指南.md) 的「私人书桌」一节。启动是手动的；登录时自动启动可以不注册。脚本输出是 ASCII，日志是 UTF-8。

公开站的「留言」页把访客的话交给 Cloudflare Pages Function，经 Turnstile 校验后暂存在 D1。书桌启动时和手动收取时用一把只许取走并确认的密钥把留言拉回本机，云上的副本随后删除。这和原来的邮件 Worker 不是同一条路：邮件仍是 HMAC 转发到本机 API，留言不经过那条收信接口。

手记和日记也在这台电脑上。日记就是带日期的手记，一天一篇。正文是 Markdown，预览前会去掉脚本和任意 HTML。照片从相册里选，图片只走登录后的照片接口。这些内容写在数据目录的同一份数据库里，每天的在线备份会一起带走。

问屿在 `http://127.0.0.1:8787/private/ask`。它只索引手记、日记、来信和照片说明，向量也在这份数据库里。嵌入和生成分别看本机的 `127.0.0.1:8001` 和 `127.0.0.1:8000`，地址可以在 `runtime.env` 里改，但必须是回环地址。模型没开时页面会说明哪一个没开，其他页面不受影响。提问时会请求关闭思考模式。登录后先到总览：待办、近况和几项数量都在首页，待办存在 schema v6。

## 本机快速开始

前置条件：Node.js 20+、pnpm 9、Python 3.12+。当前源码树包含已跟踪的静态仓库快照与空播放列表，在依赖已经安装时可直接构建。`pnpm fetch:repos` 只在需要更新 GitHub/Gitee 构建期快照时才运行。2026-11-01 已验证项目 `apps/api/.venv` 的 Python 3.14.7 可运行；不要依据早期记录重复重建环境。

```bash
pnpm install
pnpm build
```

## 生产静态制品门禁

普通 `pnpm build` 仍是本机开发构建，不要求生产域名，也不会把当前个人内容或空数据源变成可发布状态。发布候选必须使用 `pnpm build:release`：它先只验证 `PUBLIC_SITE_URL`，完成构建后再检查静态制品、SEO、私有页索引规则、占位内容和受限文件，并输出制品 fingerprint 与固定构建输入 inventory 的 SHA-256 聚合值。

`PUBLIC_SITE_URL` 必须是实际分配或配置的站点根 HTTPS URL（只允许站点根，不含端口、路径、query 或 fragment），可以使用 Pages 分配的地址或 Caddy 使用的真实域名，不能使用示例、保留或测试域名。以下为完整前端候选的构建命令；仅公开页面的 Pages 部署使用上述 `build:pages`，具体流程见部署说明：

```powershell
$env:PUBLIC_SITE_URL = Read-Host 'Actual root HTTPS URL'
pnpm build:release
```

```sh
printf '%s' 'Actual root HTTPS URL: '
IFS= read -r PUBLIC_SITE_URL
export PUBLIC_SITE_URL
pnpm build:release
```

候选中出现受检占位内容或空的个人数据源时，production gate 会故意失败；应由项目所有者提供经授权的真实内容，不能由构建伪造内容。

该门禁只检查静态制品，不能证明 DNS、TLS、Caddy、CDN、真实部署、Lighthouse、SBOM、SCA、签名、漏洞可达性，或真实内容的授权状态。

开发模式需要 API、worker、Web 三个终端。先在 `apps/api` 按 `.env.example` 配置随机密钥、管理员 Argon2id 哈希，以及你明确选择的绝对 `DATA_ROOT`；API 与 worker 必须在相同目录、使用相同配置启动。已有虚拟环境无需重复创建，已有配置文件不要覆盖。

```powershell
# 终端 1：从仓库根目录进入 API，配置准备好后运行。
Set-Location .\apps\api
& .\.venv\Scripts\python.exe -B -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --no-access-log
```

```powershell
# 终端 2：同样从仓库根目录进入 apps/api，读取与 API 相同的本机配置。
Set-Location .\apps\api
& .\.venv\Scripts\python.exe -B -m app.worker_runner
```

如果只启动 API 和 Web，知识文档会停在排队状态，后台索引和维护任务不会执行。`ADMIN_BOOTSTRAP_PASSWORD_HASH` 仅在首次创建管理员时使用，修改它不会替换已有管理员密码。

JWT 轮换时同时更新 `AUTH_JWT_ACTIVE_KID` 与 `AUTH_JWT_SIGNING_KEY`，并把上一组 `{kid: secret}` 暂存于 API 专用的 `AUTH_JWT_PREVIOUS_KEYS_JSON`；超过最长 access TTL 后移除。首次从无 kid 的旧实现升级时，既有 access token 会失效，但仍有效的 refresh session 可换取新 token。

邮件 HMAC 轮换顺序：先部署兼容新旧协议的 API，再给 Email Worker 配置安全的 `MAIL_HMAC_ACTIVE_KID`，确认旧 Worker 与 legacy 重试排空后，才轮换 `MAIL_HMAC_KEY`/active kid，并把上一把 key 暂存 API 专用的 `MAIL_HMAC_PREVIOUS_KEYS_JSON`；旧协议永远不尝试旧 key。

在第三个终端、仓库根目录启动前端：

```powershell
pnpm dev
```

访问 `http://localhost:4321`。Astro 开发服务器会把 `/api/*` 服务端代理到 `http://127.0.0.1:8000`；浏览器侧始终保持同源 `/api/v1`。如 API 不在默认地址，可仅在启动前端时设置非公开环境变量 `API_DEV_PROXY_TARGET`，例如 `http://127.0.0.1:9000`。

虚拟环境创建、外置数据根、Compose 单入口启动及备份后切换/回滚见 [本地运行与恢复指南](./docs/本地运行与恢复指南.md)。该指南是操作步骤，实际通过项以当前进度链接的验收报告为准。

## 验证

```bash
pnpm --filter @star-isle/contracts test
pnpm --filter @star-isle/web test
pnpm --filter @star-isle/web lint
pnpm build

cd apps/api
python -m pytest tests
python -m tests.e2e_smoke
```

`e2e_smoke` 在临时 DATA_ROOT 内演练文档上传、worker 索引、检索和 RAG SSE，不会写入现有运行时数据。

### 测试 harness 的隐私与证据边界

API pytest 在 collection 导入业务模块前安装固定的 test-only 密钥和密码哈希；未显式提供根目录时，它只使用并清理自己创建的系统临时 `DATA_ROOT`。调用者显式提供的 `DATA_ROOT` / `DATA_HOST_ROOT` 必须是经验证系统临时目录的严格子目录，且不能穿过 symlink/reparse point；harness 不会清理该外部目录。

`python -B -m tests.e2e_smoke` 也始终创建并清理自己的临时根，普通 stdout/stderr 只保留固定阶段、状态和安全计数。`python -B scripts/browser_e2e.py --headless` 的默认隔离栈每次在内部生成随机测试密码和邮件 HMAC；密码哈希 helper 仅从 stdin 接收密码，命令行不接受 `--password`。外部服务模式必须在启动浏览器或网络请求前从明确的 test-only 环境变量 `STAR_ISLE_E2E_TEST_PASSWORD` 与 `MAIL_HMAC_KEY` 取得两项凭据，缺失时固定失败且不回显值。

browser 默认在自有系统临时目录中交换样本、私有邮件回执、截图和 summary，并在退出时删除它们；回执和 RAG 显式报告均排除仓库、默认 `data/`、`DATA_ROOT` 与 `DATA_HOST_ROOT`，且使用非 reparse、排他创建。需要保留 browser 证据时，才可传入一个**尚不存在**、且不在这些受保护根内的系统临时目录：`--evidence-dir <new-system-temp-directory>`（兼容 `STAR_ISLE_E2E_EVIDENCE`）。目标由 child 排他创建，既有目录不会复用、覆盖或清理；截图可能包含私有 UI，未显式 opt-in 时不得持久化。完整 browser E2E 的 API/Web 必须都是无 userinfo/path/query/fragment 的 loopback origin；只有 `--public-only` 才可检查明确指定的远程公开静态站点。普通输出中的路径是固定路由模板（未知路径为 `/other`），不保留动态 ID、对象键或文件名。自有进程在 Windows 使用本轮专属 Job Object：先以 CPython 隔离启动模式（`-I -S -B`）运行不执行目标的 gate bootstrap，assignment 成功后才放行目标；bootstrap helper 必须是非 symlink/reparse 的普通文件。原目标在 gate 后仍继承调用方的环境、cwd 与标准 streams。任一树、pipe、端口或浏览器 close 的清理失败都使运行失败。

现有 `scripts/.e2e-browser-evidence` 与历史系统临时目录不属于 harness 所有权，未被读取、删除或清理。

`scripts/public_js_budget.py` 默认只访问严格 loopback origin；非 loopback 的公开静态站点必须显式给出 `--allow-remote-public`，且该模式不加载私有 fixture 或凭据。`scripts/rag_eval.py` 的 synthetic-smoke 只允许空 embedding endpoint/hash 离线 profile；acceptance 仍要求 `--authorized`，远程 embedding 另须 `--allow-remote-embedding`。这些 harness 边界不会改变 RAG 的 keyword/vector retrieval、指标或 acceptance 判定策略；显式 RAG 报告仍保持既有授权输出结构，可能包含已授权的 provenance。

## 本地结构化运维事件

API、`/health/ready`、worker、邮件接收的审计/对象清理失败旁路、邮件保留期、通用 asset GC、幂等清理和审计保留期只向 stdout 输出单行 JSONL 状态。事件顶层固定包含 UTC 时间、level、component、event 和 result；按组件只会附加经过白名单验证的请求标识、HTTP 方法/路由模板/状态、ready 的 `db`/`storage` 布尔值、稳定错误码、审计旁路状态或非负聚合计数。它不会记录请求原始 URL/query/header/body、Cookie/JWT/HMAC、邮件/文档内容、对象键、文件路径、哈希、模型标识或 secret。

邮件接收失败旁路仅使用固定 `mail` component 的 `audit_unavailable` 或 `object_cleanup_unavailable` 事件、`failed` result 与 `ERROR` level；审计失败时最多附加一个已校验的 `requestId`，不会携带异常原文或邮件、MIME、对象与路径数据。

`X-Request-Id` 仅在完整匹配 ASCII `[A-Za-z0-9][A-Za-z0-9._:-]{0,63}` 时回显；其余输入会替换为本地生成的 12 位小写十六进制值。未处理异常始终返回固定 500，并以 `INTERNAL` 审计结果旁路记录，不输出异常原文。项目自带的 Uvicorn 启动命令使用 `--no-access-log`，由上述脱敏模板事件替代原始路径/query access log。

`/health/ready` 的公开 JSON 仍仅包含 `status` 和 `checks:{db:boolean,storage:boolean}`；storage healthcheck 继续使用临时 `objects/health` 写探针并立即清理。该目录不是运行时业务对象，也不会进入当前 format v3 备份。

## Compose 同源入口

`infra/compose/compose.yml` 是**内部基础层**，不可单独启动。每次都必须与
`compose.local.yml` 或 `compose.prod.yml` 合并，并先运行不会 build、pull、up 或创建
容器的离线预检。预检只用系统临时目录中的合成 env/data/model 路径执行
`docker compose ... config`，不读取 `apps/api/.env` 或仓库 `data/`。

```powershell
pnpm build
& .\apps\api\.venv\Scripts\python.exe -B .\scripts\deployment_preflight.py
```

### 本机 loopback 覆盖层

`DATA_HOST_ROOT` 必须是一个已存在的、显式绝对的隔离或持久目录，绝不能回退到仓库
`data/`。本机覆盖层只发布到 `127.0.0.1`；API env_file 默认是
`apps/api/.env` 的 optional/raw 路径，也可用 `STAR_ISLE_API_ENV_FILE` 指向另一个
本机开发配置文件。`MAIL_RETENTION_DAYS`、`IDEMPOTENCY_RETENTION_DAYS` 与 `EMBEDDING_DIM` 是 API/worker 共用的
Compose topology 变量（默认分别为 `30`、`30`、`384`）；Compose 会覆盖 API env_file 中的
同名值，避免 worker 与 API 的保留期或 hash-v1 维度漂移。worker 另注入 `AUDIT_RETENTION_DAYS`
与 `AUDIT_SOFT_CAP_ROWS`（默认 `90`、`100000`）；audit 保留期清理由 worker 执行，
API 不读取这两个变量。caddy/api/worker 均显式声明 json-file 日志轮转（`10m` × `3`）。

```powershell
$env:DATA_HOST_ROOT = 'D:\star-isle-runtime'
$env:CADDY_HOST_PORT = '8080' # 可选；仍只绑定 127.0.0.1
docker compose -f infra/compose/compose.yml -f infra/compose/compose.local.yml config --quiet
docker compose -f infra/compose/compose.yml -f infra/compose/compose.local.yml up -d --build
```

基础服务为 `api`、`worker` 和 `caddy`；`/api/v1/health/live` 与
`/api/v1/health/ready` 由 Caddy 同源转发，未知静态路由返回真实 404。停止时也必须带上
相同的覆盖层：

```powershell
docker compose -f infra/compose/compose.yml -f infra/compose/compose.local.yml down
```

模型容器是可选 CPU profile，基础栈不依赖 GGUF。CPU 模式还必须提供已存在、只读、授权的
`MODEL_HOST_ROOT`；它不会默认挂载仓库 `data/models`，并会把 API/worker 的模型 URL
覆盖为内部服务名：

```powershell
$env:MODEL_HOST_ROOT = 'D:\star-isle-models'
docker compose -f infra/compose/compose.yml -f infra/compose/compose.local.yml -f infra/compose/compose.cpu.yml --profile cpu up -d --build
```

没有模型时，RAG 会返回明确标注的本地降级回答，而不是阻止 readiness。`compose.cpu.yml`
没有 GPU profile，也不会发布模型服务端口。

### 生产覆盖层

生产模式只允许 Caddy 发布宿主端口（默认 `80/tcp`、`443/tcp`、`443/udp`）。必须提供真实、
非默认的域名和 ACME 邮箱，以及由部署系统管理的外部 API env_file；该文件应满足生产
`STAR_ISLE_ENV=production` 的 fail-closed 密钥、Argon2id、`COOKIE_SECURE=true` 与显式
`DATA_ROOT` 规则。不要把这些 secret 复制到 `infra/compose/.env.example` 或仓库中。

```powershell
$env:DATA_HOST_ROOT = 'D:\star-isle-production-runtime'
$env:STAR_ISLE_SITE_ADDRESS = 'portal.example.com'
$env:ACME_EMAIL = 'ops@example.com'
$env:STAR_ISLE_API_ENV_FILE = 'D:\secure-config\star-isle-api.env'
docker compose -f infra/compose/compose.yml -f infra/compose/compose.prod.yml config --quiet
docker compose -f infra/compose/compose.yml -f infra/compose/compose.prod.yml up -d --build
```

当前本机 Caddy 已完成基础 HTTP/浏览器联调及 SSE 首条 data/无压缩检查，见上方隔离联调报告。
本机受控上游的 10 秒 heartbeat、取消/断连与错误不保存也已通过。主动刷新、生产 TLS/ACME、CDN 与外部服务仍需验收，不能将本机结果推广为实际生产链路全部通过。

## 备份、隔离恢复与校验

备份只包含 SQLite 一致性快照与由该快照逻辑引用的对象；不会复制 `.env`、模型权重、日志、`objects/health`、任何未引用 orphan（包括 `mail-raw`）或旧备份。manifest 只记录 secret 环境变量名称，不记录其值；`objectCount` 是逻辑清单中唯一物理对象的数量。

备份会先以 source SQLite 的 `BEGIN IMMEDIATE` 协调锁阻断协作型写者（默认最多等待 15 秒），再用独立只读连接执行 SQLite online backup，并在锁持有期间按 destination snapshot 的 asset / `mail_raw` 引用复制对象。新备份不会包含 `-wal`、`-shm` 或 journal sidecar。若 output 中仍有内部 incomplete marker，`verify` 和 `restore` 会拒绝它；失败的 partial 会保留 marker 和已有文件，必须由运维显式检查/处理，不能复用或覆盖。

```powershell
# 在仓库根目录运行。输入实际运行根，不使用仓库相对 data。
$runtimeRoot = (Resolve-Path -LiteralPath (Read-Host '当前实际 DATA_ROOT / DATA_HOST_ROOT 的绝对路径')).Path
$backupRoot = Read-Host '数据根之外、新建备份目录的绝对路径'
$restoredRoot = Read-Host '数据根和备份之外、新建恢复目标的绝对路径'
& .\apps\api\.venv\Scripts\python.exe -B .\scripts\backup_restore.py backup --data-root $runtimeRoot --output $backupRoot --verify --lock-timeout-seconds 15 --json
if ($LASTEXITCODE -ne 0) { throw '备份失败，先检查保留的失败状态' }
& .\apps\api\.venv\Scripts\python.exe -B .\scripts\backup_restore.py restore --backup $backupRoot --target $restoredRoot --json
if ($LASTEXITCODE -ne 0) { throw '恢复失败，不要启用该目标' }
& .\apps\api\.venv\Scripts\python.exe -B .\scripts\backup_restore.py verify --backup $backupRoot --data-root $restoredRoot --json
if ($LASTEXITCODE -ne 0) { throw '恢复校验失败，不要启用该目标' }
```

上述为宿主机已有 API 虚拟环境时的路径。输出与恢复目标必须由你明确选择、彼此独立，不得包含或落入实际数据根。也提供 `scripts/backup.bat`、`restore.bat`、`verify-restore.bat` 与对应 `.sh` 薄包装，它们使用当前 `python`/`python3`，须先确认运行环境。仅有 Docker 的用户按 [容器操作步骤](./docs/本地运行与恢复指南.md#在-linux-容器中备份和隔离恢复) 显式挂载脚本；API 镜像默认不包含 `scripts/`。

`backup`、`restore`、`verify` 默认保留原有人类可读 stdout/错误行为。加 `--json` 后，领域成功或可预期失败只输出一行脱敏 JSON：成功包含 tool、command、status 与已严格验证的 format/schema/file/object 计数；失败只包含固定的 command-specific errorCode，退出码仍为 0/1。argparse 用法错误不属于该 JSON 保证。

### 受控隔离恢复演练

`scripts/recovery_drill.py` 是本机候选备份的受控恢复演练入口。它只接受已完成的 format v3、schema v5 备份；format v3 的 manifest 与实际 `objects/` 只允许 `originals`、`thumbnails`、`mail-attachments`、`mail-raw`，因此 `objects/health`（即使为空）或其他不完整痕迹都会被拒绝。它会先做严格的备份 verify，只有实际文件集合、reparse/链接与数据库安全检查通过后才读取候选数据库执行照片/知识预检；随后在新的隔离目标中恢复，并在子进程中针对**恢复目标**调用现有 `rag_engine.search`。

任何成功 report 固定标记 `scope=isolated-local-candidate` 与 `productionAccepted=false`：它是隔离本地候选的证据，不是生产恢复、部署或 M1/M2/M3 完成声明。

从仓库根目录使用 API 虚拟环境运行。`--backup`、`--target` 和 `--report` 都必须是绝对路径；三者都必须与仓库默认 `data/` 和显式 `DATA_ROOT` 隔离，backup 既不能位于这些运行时根内、也不能包含这些根。target 和 report 必须不存在，report 的父目录必须已存在；三者不得彼此重叠。检索查询只能从标准输入传入（UTF-8、1–4096 字节），不会作为命令行、stdout 或 report 字段保存。

```powershell
Set-Location 'D:\project file\StarIsle'
$backup = 'D:\recovery-input\star-isle-backup'
$target = 'D:\recovery-work\star-isle-target'
$report = 'D:\recovery-reports\star-isle-drill.json'

'一条已知会命中候选知识库的非敏感查询' | & .\apps\api\.venv\Scripts\python.exe -B .\scripts\recovery_drill.py --backup $backup --target $target --report $report --knowledge-query-stdin
```

演练不启动 API、worker 或模型服务，也不调用本项目的 HTTP/RAG API。候选必须已有至少一张可关联的照片、至少一份 `ready` 知识文档，以及与恢复 target DB 精确匹配的单一可用 `sqlite-vec` 向量 profile。本地未配置 embedding endpoint 时可使用 `hash-v1`；若该 profile 依赖远程 embedding，则 endpoint 与 model 必须已配置且可用。缺少依赖、profile 不匹配、endpoint/model 不可用或检索零命中都会失败，不会伪造关键词或模型降级结果。它只证明该隔离候选的已存储检索链路。真实授权语料、真实模型/profile、部署配置和生产流量仍须在各自受控的验收中验证。

在 report path claim 成功后，runner 会原子创建 `<report>.star-isle-recovery-drill-incomplete` marker 并发布 `running` report；成功时才原子写入 `passed` 并最后删除 marker。此后的失败、异常或 marker 删除失败会保留 marker 与已创建的 target/report partial，禁止复用或覆盖。运维人员必须先检查这些**精确的隔离路径**，确认 backup、target、report 都不与默认 `data/` 或 `DATA_ROOT` 重叠，再显式删除 target、report、marker 和可能的 `<report>.star-isle-recovery-drill.tmp`；runner 从不替代该人工清理决定。stdout 仅输出一行脱敏 JSON，report 也不包含查询、对象键、路径、邮件正文或模型/密钥细节。

该门禁只能协调遵守 SQLite 写锁和对象写入顺序的本服务写者。手工或其他非协作进程仍可能在复制期间直接修改文件；长于 API 默认 15 秒的备份也可能令写请求超时。隔离候选演练不等于真实生产恢复演练，也不表示 M2 已完成。

生成恢复目标后还需启动同一版本的 API/worker，并核对登录、文件下载、原图/缩略图和知识检索，才能判断该目标是否可用。切换与失败回滚使用新的 `DATA_HOST_ROOT`，保留原运行目录；完整命令见 [恢复后切换和回滚](./docs/本地运行与恢复指南.md#恢复后切换和回滚)。本轮实际验收另见 [网站交付与本地恢复验收](./docs/2026-11-03_网站交付与本地恢复验收.md)。

## 当前外部边界

- 2026-11-01 已在首批实现与本轮修正后分别运行完整隔离浏览器 E2E，均为 12 组、0 失败；当前 Pages 公开站已真实部署并完成线上交互、HTTP 和移动 Lighthouse。本机 Caddy 的基础同源及受控上游长流/取消链路已通过；主动刷新、生产 TLS、邮件 Cloudflare 平台链路、真实业务账号与模型仍待补验。历史 production-preview 结果保持历史属性，详见当前实施进度。
- `scripts/email-worker.js` 为 Cloudflare Worker 参考实现；失败会拒收并仅记录脱敏状态，实际 Cloudflare 平台重试/退信行为仍需在真实账号中验证。
- 个人内容和仓库快照已部分补齐，仍由项目所有者按授权与真实性要求维护；当前空歌单的播放器入口已隐藏，其他空列表按组件提供空态。

## 许可证

本项目基于 [MIT License](./LICENSE) 开源。
