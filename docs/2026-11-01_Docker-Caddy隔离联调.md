# Docker / Caddy 隔离同源联调

更新：2026-11-01。Docker Desktop 已恢复，使用既有 Compose base/local 与 Caddyfile，先完成独立临时栈的真实 HTTP 链路，再以另一临时栈补充 Chromium 登录/退出。没有部署云端私有服务，没有读取项目真实 .env/data 或模型。

## 结果与范围

| 检查 | 实际结果 |
| --- | --- |
| 引擎 | 初始 Linux 引擎管道不可用；以隐藏窗口启动已安装的 Docker Desktop 后，Server 29.8.2 可用，context=desktop-linux |
| Compose | 5.6.0；此前四种合成 config 预检 89 项通过 |
| 隔离服务 | api、worker、caddy 三个服务，独立随机项目名 starisleaccept94704fe33f |
| 端口 | 仅 Caddy 的容器 8080 映射 127.0.0.1:61513；API/worker 的 8000 仅为容器内部端口，未映射宿主 |
| 静态与匿名守卫 | 首页/登录页 200，默认完整登录表单存在；未知路径 404；匿名 auth/me 401 |
| Cookie / CSRF 登录 | 经同一 Caddy 地址取得 CSRF 后登录 200，Cookie 含 HttpOnly，登录态 auth/me 200 |
| 实际 worker | API 接受受控 Markdown 后，独立 worker 将其处理至 indexState=ready |
| SSE / 引用 | 经 Caddy 的降级检索问答返回 stage/token/citation/done；Content-Type 为 event-stream，无 Content-Encoding；首条 data 270.61ms，引用 1 条 |
| CSRF 负例 | 登录态上传但不提供 CSRF 时为 403，知识文档数量仍为 1，无新增残留 |
| 登出 | logout 204，随后 auth/me 401 |
| 清理 | 本轮容器、命名卷、网络均核对为 0；独立临时数据/前端/env 目录已删除；本轮镜像标签已移除 |

这里的 SSE 为无真实模型的检索/降级路径，不能解释为真实 chat/embedding 模型能力。没有真实生产 TLS / Secure Cookie 验收；本地覆盖层明确使用 HTTP 与 COOKIE_SECURE=false。

## 实际浏览器补验

在另一独立项目 `starisleaccept22f649a9f8`、入口 `127.0.0.1:52022` 中，使用实际 Chromium（1440×900、reduced-motion）从信封键盘进入登录页，输入本轮随机测试密码，确认登录 API 200、控制台概览可见。access/refresh 两种 Cookie 均为 HttpOnly、SameSite=Lax；本地 HTTP 的 Secure=false，不作为生产 TLS 验收。

点击退出后 API 为 204，浏览器回到首页，两种登录 Cookie 均被移除；再次访问控制台会跳到登录页并显示密码输入框。该流程没有 pageerror；独立 HTTP 会话同时重跑静态/匿名/CSRF、知识上传与 worker、SSE 引用及登出检查，首条 SSE data 为 458.18ms、引用 1 条，未压缩。

此轮 API 镜像 ID 为 `sha256:60577efcb0eea1185ce7c97518058e4a8d95d199441b679bbeea8d28b92e41da`，Caddy digest 与前轮相同。浏览器及自有测试栈已退出，命名卷/网络/临时目录和镜像标签已清理。没有测试主动刷新、长流 heartbeat 或取消/断连传播，因此 S2-02 仍只部分完成。

## 如何保持隔离

复用现有 infra/compose/compose.yml 与 compose.local.yml，临时 JSON 覆盖层只指定本轮独立镜像、restart=no 和临时前端挂载。没有修改现有基础配置或连接 CPU 模型 profile。

验收脚本创建并校验系统临时目录，显式给出 DATA_HOST_ROOT、STAR_ISLE_API_ENV_FILE、独立项目名和空闲 loopback 端口。密码/JWT/HMAC 由本轮随机生成，API env_file 只指向该临时文件；配置日志中的测试签名值及密码哈希已脱敏。worker 不接收管理员哈希或 JWT/HMAC 密钥。

默认完整前端使用 Astro --outDir 构建到临时目录，启用本地登录，PUBLIC_API_BASE=/api/v1；没有覆盖公开 Pages 的 dist。结束后重跑原发布门禁，公开候选仍为 55 文件，SHA-256 `9e29df03aa211bae144ec555f854c3cf5de75db4ee518fd2f0286727b83f85b0`，与已发布版本一致。

API 镜像仅从 apps/api 的 Dockerfile、requirements 与 app 源码构建，现有 deny-by-default .dockerignore 排除了 .env、私钥、虚拟环境、测试和缓存。实际镜像 ID 为 `sha256:da9e23c0078afd5721cbede6550c76954a6bd028dd7c523e538ae51406f3605d`；Caddy 镜像记录为 `caddy@sha256:36c24ad91a7ff7bc4c87fe22bfa8df3c4b18da0773d73fc12ee2c52b98c0bb7e`。没有修改依赖声明或锁文件；镜像按现有 requirements 解析 Linux 依赖。

首次构建运行时直接探测到 Python 3.12.16、SQLite 3.46.1、FastAPI 0.162.0、Pydantic 2.14.2、sqlite-vec 0.1.9；第二轮沿用相同源码及依赖缓存。该探测保存在首次尝试证据中，不冒充完整 Linux API 单元测试回归。

## 失败记录

首次 api/worker/caddy 已启动，Compose up --wait 成功，但一次性验收脚本将 compose ps --format json 当作一个 JSON 文档解析；当前 Compose 输出多行 JSON，因此报 JSONDecodeError Extra data。首次测试栈已在 finally 中停止并清理，证据移动至 docker-attempt-1，没有更改应用逻辑迎合测试。

修正验收脚本以兼容 JSON 数组、单对象与多行 JSON 后，第二轮完整 HTTP 链路和资源清理通过。没有覆盖首次失败记录，没有将第一次“启动成功”写成全链路验收通过。

首次浏览器补验已实际登录控制台，但脚本用精确标题“概览”匹配时未包含装饰前缀“//”，导致定位超时；失败栈已清理，证据保留于 docker-browser-attempt-1。将本机脚本定位器改为匹配标题结尾、等待页面加载后，补验完整通过；没有为此修改产品代码或移除登录/退出检查。

## 本机证据与复查

证据在忽略目录 logs/web-perf-20261101-561ef4e：

- docker-acceptance-result.json：阶段、端口、镜像 ID、首条 SSE 时间与清理核对。
- docker-acceptance.cli.log 与 docker-*.log：构建、启动、状态、服务日志及退出过程；测试配置签名值已脱敏。
- docker-attempt-1/：保留的第一次状态解析失败及运行时探测。
- docker-browser-extension/ 与 docker-browser-extension.cli.log：实际浏览器补验、重跑 HTTP 链路及清理结果；只记录 Cookie 属性，不记录 Cookie 值。
- docker-browser-attempt-1/：保留的首次浏览器标题定位失败。
- docker_acceptance.py：本机一次性隔离验收脚本，复用 scripts/harness_safety.py 的临时目录检查；不属于本次应用源码变更或已推送提交。

在本机仓库根目录可运行留存的基本 HTTP 验收脚本，每次使用新的临时目录与 Compose 项目名；基本模式会覆盖同名本机报告，需要留存旧证据时先归档。`--browser` 模式要求 docker-browser-extension 证据目录尚不存在，本轮结果已占用该目录，不应直接原地重跑：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
.\apps\api\.venv\Scripts\python.exe -B logs/web-perf-20261101-561ef4e/docker_acceptance.py
```

这些是本机证据，受既有 logs 忽略规则保护，不提交测试配置、原始输出、数据库或镜像。Docker Desktop 保留运行作为开发基础设施；本轮应用测试栈及宿主入口均已关闭，没有全局 prune、停止其他项目或删除共享镜像缓存。

## 后续仍需验证

本报告记录基础联调当时的覆盖范围。之后已另批完成实际 10 秒心跳、HTTP/浏览器取消与导航、上游 partial 错误等检查，并修复运行告警请求头记录；后续结果见 [Caddy 长流与日志脱敏](./2026-11-01_Caddy长流与日志脱敏.md)，下方原范围描述保留为基础批次边界。

S2-01 的隔离启动和清理已有直接证据。S2-02 的实际浏览器 Cookie/CSRF 登录、控制台、退出后拒绝访问，以及 SSE 首条数据/无压缩已验证，但主动刷新、长流 heartbeat、主动取消和断连传播未在此次容器链路覆盖。S2-03 仅覆盖知识文档上传和 CSRF 负例，不能代替文件/照片、配额和所有格式负例。

实际容器下的备份/隔离恢复/回滚、生产 TLS、真实邮件域名与平台链路、真实模型/授权语料/设备仍待后续完成。没有把已有 Windows 本地 608 项或 12 组隔离浏览器结果冒充 Linux 容器全量回归；M2/M3 保持未验收。

## Evidence → Finding → Path

| Evidence | Finding | Path |
| --- | --- | --- |
| Engine 29.8.2、89 项 config 预检和三服务状态 | 现有配置可以在本机 Linux 容器下实际启动 | 基础环境已恢复，继续同一隔离方式补其他运行场景 |
| 经 Caddy 的登录/入库/worker/SSE/CSRF/登出 HTTP | 基本同源功能链路成立，首条 SSE 270.61ms 且无压缩 | 后续补长流/取消/恢复，不将降级回答视为真实模型验收 |
| Chromium 经同一 Caddy 入口登录控制台、退出清 Cookie、再次访问被拒绝 | 浏览器实际 Cookie 与导航链路通过，补验 SSE 458.18ms | 主动刷新与生产 Secure Cookie 仍需独立验收 |
| 标签过滤查询为空、临时目录不存在、镜像标签移除 | 本轮自有资源已退出且公开候选未改变 | 保留忽略目录中的证据，后续工作不接管真实运行数据 |
