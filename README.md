# 星屿 StarIsle

个人全栈门户：公开展示站点（星）与受 Cookie 保护的私有空间（屿）。前端使用 Astro，私有 API 使用 FastAPI；浏览器只请求同源 `/api/v1`，JWT 只保存在 Secure/HttpOnly Cookie 中。

本仓库的四份规划文档仍是计划基线；本 README 只说明本机可重复的开发、验证和恢复流程，不代表已完成生产部署或外部服务验收。

## 本机快速开始

前置条件：Node.js 20+、pnpm 9、Python 3.12+。当前源码树包含静态仓库快照与空播放列表，在依赖已经安装时可直接构建；仓库尚未建立首个 Git commit，不能将这些文件称为已受版本控制。`pnpm fetch:repos` 只在需要更新 GitHub/Gitee 构建期快照时才运行。

```bash
pnpm install
pnpm build
```

## 生产静态制品门禁

普通 `pnpm build` 仍是本机开发构建，不要求生产域名，也不会把当前个人内容或空数据源变成可发布状态。发布候选必须使用 `pnpm build:release`：它先只验证 `PUBLIC_SITE_URL`，完成构建后再检查静态制品、SEO、私有页索引规则、占位内容和受限文件，并输出制品 fingerprint 与固定构建输入 inventory 的 SHA-256 聚合值。

`PUBLIC_SITE_URL` 必须是已经由 Caddy 使用的真实根 HTTPS URL（只允许站点根，不含端口、路径、query 或 fragment），不能使用示例、保留或测试域名。以下命令会在终端读取这个已部署域名，避免把虚构域名写进发布命令：

```powershell
$env:PUBLIC_SITE_URL = Read-Host 'Actual Caddy root HTTPS URL'
pnpm build:release
```

```sh
printf '%s' 'Actual Caddy root HTTPS URL: '
IFS= read -r PUBLIC_SITE_URL
export PUBLIC_SITE_URL
pnpm build:release
```

当前占位内容或空的个人数据源会故意使 production gate 失败；这表示发布前仍需由项目所有者提供经授权的真实内容，而不是由构建伪造内容。

该门禁只检查静态制品，不能证明 DNS、TLS、Caddy、CDN、真实部署、Lighthouse、SBOM、SCA、签名、漏洞可达性，或真实内容的授权状态。

在一个终端启动 API：

```bash
cd apps/api
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# 设置随机 AUTH_JWT_SIGNING_KEY、MAIL_HMAC_KEY，
# 并以 Argon2id 写入 ADMIN_BOOTSTRAP_PASSWORD_HASH（不要填写明文密码）。
python -m uvicorn app.main:app --port 8000 --reload --no-access-log
```

JWT 轮换时同时更新 `AUTH_JWT_ACTIVE_KID` 与 `AUTH_JWT_SIGNING_KEY`，并把上一组 `{kid: secret}` 暂存于 API 专用的 `AUTH_JWT_PREVIOUS_KEYS_JSON`；超过最长 access TTL 后移除。首次从无 kid 的旧实现升级时，既有 access token 会失效，但仍有效的 refresh session 可换取新 token。

邮件 HMAC 轮换顺序：先部署兼容新旧协议的 API，再给 Email Worker 配置安全的 `MAIL_HMAC_ACTIVE_KID`，确认旧 Worker 与 legacy 重试排空后，才轮换 `MAIL_HMAC_KEY`/active kid，并把上一把 key 暂存 API 专用的 `MAIL_HMAC_PREVIOUS_KEYS_JSON`；旧协议永远不尝试旧 key。

在另一个终端启动前端：

```bash
pnpm dev
```

访问 `http://localhost:4321`。Astro 开发服务器会把 `/api/*` 服务端代理到 `http://127.0.0.1:8000`；浏览器侧始终保持同源 `/api/v1`。如 API 不在默认地址，可仅在启动前端时设置非公开环境变量 `API_DEV_PROXY_TARGET`，例如 `http://127.0.0.1:9000`。

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

真实部署前仍须在受控环境实测 Caddy 的 SSE first-byte、heartbeat 和客户端取消语义；本轮的
预检只证明静态配置候选，不能替代 Caddy 运行验收、TLS/ACME、CDN 或外部服务验收。

## 备份、隔离恢复与校验

备份只包含 SQLite 一致性快照与由该快照逻辑引用的对象；不会复制 `.env`、模型权重、日志、`objects/health`、任何未引用 orphan（包括 `mail-raw`）或旧备份。manifest 只记录 secret 环境变量名称，不记录其值；`objectCount` 是逻辑清单中唯一物理对象的数量。

备份会先以 source SQLite 的 `BEGIN IMMEDIATE` 协调锁阻断协作型写者（默认最多等待 15 秒），再用独立只读连接执行 SQLite online backup，并在锁持有期间按 destination snapshot 的 asset / `mail_raw` 引用复制对象。新备份不会包含 `-wal`、`-shm` 或 journal sidecar。若 output 中仍有内部 incomplete marker，`verify` 和 `restore` 会拒绝它；失败的 partial 会保留 marker 和已有文件，必须由运维显式检查/处理，不能复用或覆盖。

```bash
# 输出目录必须不存在或为空，且不能放进 DATA_ROOT。
python scripts/backup_restore.py backup --data-root data --output D:\star-isle-backup --verify --lock-timeout-seconds 15

# 恢复目标必须不存在或为空；默认拒绝覆盖非空目录。
python scripts/backup_restore.py restore --backup D:\star-isle-backup --target D:\star-isle-restore-test
python scripts/backup_restore.py verify --backup D:\star-isle-backup --data-root D:\star-isle-restore-test

# 可选机器可读结果：stdout 恰一行 JSON，不输出路径、对象键、哈希或失败原文。
python scripts/backup_restore.py verify --backup D:\star-isle-backup --data-root D:\star-isle-restore-test --json
```

也提供 `scripts/backup.bat`、`restore.bat`、`verify-restore.bat` 与对应 `.sh` 薄包装，参数与上面子命令相同。

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

## 当前外部边界

- 本地隔离浏览器的 Cookie+CSRF 登录链路，以及可索引公开页的 production-preview Lighthouse 四维验收已完成；这不等同于真实部署验收。真实部署域名、CDN、Caddy 同源、Cloudflare、真实账号与模型仍未验收。
- `scripts/email-worker.js` 为 Cloudflare Worker 参考实现；失败会拒收并仅记录脱敏状态，实际 Cloudflare 平台重试/退信行为仍需在真实账号中验证。
- 个人内容、音乐和仓库来源仍由项目所有者按授权与真实性要求维护；空列表会显示空态，不会伪造数据。
