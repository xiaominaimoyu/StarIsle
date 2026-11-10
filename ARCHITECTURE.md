# 当前架构与变更边界

更新：2026-11-10。详细基线见 [总体具体开发计划](./docs/星屿%20StarIsle%20-%20总体具体开发计划.md)。

## 2026-11-10 后端修复边界

私有 API 仍只通过同源 `/api/v1` 与 HttpOnly Cookie 工作。直连 uvicorn 现在也返回 `nosniff`、`DENY`、`no-referrer` 和默认 `no-store`；已经自带 `Cache-Control` 的 SSE 不改写。生产环境关闭 `/api/docs`、`/api/openapi.json` 与 `/redoc`。照片内容与声明不符返回 415 `UNSUPPORTED_MEDIA_TYPE`；超过 8000 字的 RAG 问题仍是 422 `UNPROCESSABLE`，消息改为“问题过长，请缩短后重试。” schema 与依赖未改。

## 技术栈与目录

| 路径 | 当前职责 |
| --- | --- |
| apps/web | Astro 5 静态页面、React 19 islands、Tailwind 4；公开内容、信封入口与私有控制台 |
| packages/contracts | 前后端共享 TypeScript 契约 |
| apps/api | FastAPI 同源 /api/v1、Cookie/CSRF、SQLite schema v6 与本地对象存储 |
| apps/api/app/worker_runner.py | durable job、文档索引与维护任务 |
| infra | Caddy 和 Compose base/local/prod/cpu 覆盖层 |
| scripts | 浏览器/后端验收、邮件 Worker、发布预检、备份恢复和 RAG 评测 |

## 数据流

公开 Markdown 与仓库/精选/歌单 JSON → Astro 内容集合/构建 → 静态页面及必要 islands。私有 Web → 同源 /api/v1 → Cookie/CSRF → API → SQLite/对象存储。文档任务由 worker 消费，RAG 检索与内部 chat/embedding 服务分别对接。

## 当前批次边界

此前公开站和 S2 批次结果保留。当前仅完善 LoginForm/FloatInput/SessionGate 的已确认交互和恢复问题，沿用现有 session store 与鉴权 API；进入控制台时只在已有明确 401 路径调用 refresh，CSRF 失败先清除前端失效缓存，由用户重试重新握手。不改 API 契约、数据库 schema、依赖或私有数据，不把写请求加入自动重放。

已核对 Astro 5.18.2 的 glob-loader：同一文件 digest 变化时，重新解析前 store 已含该 ID，会触发重复提示；当前三个集合的源文件与缓存条目为 1/1/2，没有重复条目或内容丢失。本批保留内容加载配置与依赖，单独记录该诊断行为。验证与证据回写到 [PROGRESS.md](./PROGRESS.md) 和开发进度。

## 本机私人书桌

2026-11-10 起，主人每天使用的书桌不走 Astro 公开构建。页面、样式和脚本放在 `apps/api/app/private_desk/`，由 `python -m app.private_server` 和 `/api/v1` 一起绑在 `127.0.0.1:8787`。公开 `pnpm build:pages` 不读取这个目录。账号由 `python -m app.owner_setup` 写入数据目录，没有注册路由。没有终端时该命令直接退出；Windows 启动脚本不重定向原生 stderr，日志由进程自己写成 UTF-8。登录自动启动是可选的。相册复用现有照片接口；开机备份用 SQLite backup API 把快照写到单独的备份目录，并增量复制照片。来信从公开站的 Pages Function 取回：Turnstile 和频率限制在云端，拉取密钥只能收取并确认删除。手记和日记写在同一份 SQLite 里，不另开库，也不提升 schema 版本；一篇日记对应一个日期。全文检索把正文切成二字和三字后再交给 FTS5。照片只引用相册里的编号，预览里的图片地址仍是要登录的 `/api/v1/photos/{id}/original`。`updated_at` 和 `content_hash` 用来增量索引手记。问屿把向量存在同一份库里，只访问回环地址上的 BGE-M3 和 Qwen。总览仍只占位。旧的邮件 Worker 继续把邮件 HMAC 转发到本机，不承接留言、手记或问答。

## Cloudflare 公开部署

`pnpm build:pages` → PUBLIC_SITE_URL 配置门禁 → 以 PUBLIC_PRIVATE_AREA_ENABLED=false 构建 → 写入 `_redirects` / `_headers` → 原静态发布门禁 → Wrangler Direct Upload → Cloudflare Pages。只有该构建关闭登录表单；默认构建不改变认证行为。控制台路径由 Pages 302 转至 `/login/` 提示页，API 没有云端代理。

Pages 项目为 starisle，生产分支标记为 main，地址为 https://starisle-6bt.pages.dev/。上传的是当前未提交源码构建出的静态文件，不自动推送 Git，也不部署 FastAPI、SQLite、对象存储、worker 或模型。保留原 Caddy/Compose 路线供私有栈后续决策，当前不迁移数据库至 D1 或文件至 R2。

11-01 后续已通过本机 Docker/Caddy 基础隔离联调：正常私有模式的前端以 --outDir 构建到自有临时目录，Caddy 仅绑定 loopback；API 与 worker 共用该临时数据根，模型 URL 保持空。该正常模式产物不覆盖 Pages 的公开 dist，也不直接上传 Pages。实际本地运行与清理证据见 [Docker-Caddy 隔离联调](./docs/2026-11-01_Docker-Caddy隔离联调.md)，云端私有栈仍未部署。

S2 长流验收在独立临时覆盖层内增加仅容器网络可达的慢速 chat fixture，仅覆盖 API 的 chat URL；worker/embedding 仍不连接模型。Caddy 与 API 的既有路由实际运行，浏览器使用原生 fetch 与取消按钮。fixture 只记受控场景的连接计数，不保存提示词、Cookie 或凭据；该服务不加入正式 Compose 基础层。因实测断流告警会带 CSRF 请求头，Caddy 默认运行 logger 增加 request.headers 过滤；原流式转发配置与 API 契约不变。
