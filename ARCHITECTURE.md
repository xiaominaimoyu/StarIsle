# 当前架构与变更边界

更新：2026-11-01。详细基线见 [总体具体开发计划](./docs/星屿%20StarIsle%20-%20总体具体开发计划.md)。

## 技术栈与目录

| 路径 | 当前职责 |
| --- | --- |
| apps/web | Astro 5 静态页面、React 19 islands、Tailwind 4；公开内容、信封入口与私有控制台 |
| packages/contracts | 前后端共享 TypeScript 契约 |
| apps/api | FastAPI 同源 /api/v1、Cookie/CSRF、SQLite schema v5 与本地对象存储 |
| apps/api/app/worker_runner.py | durable job、文档索引与维护任务 |
| infra | Caddy 和 Compose base/local/prod/cpu 覆盖层 |
| scripts | 浏览器/后端验收、邮件 Worker、发布预检、备份恢复和 RAG 评测 |

## 数据流

公开 Markdown 与仓库/精选/歌单 JSON → Astro 内容集合/构建 → 静态页面及必要 islands。私有 Web → 同源 /api/v1 → Cookie/CSRF → API → SQLite/对象存储。文档任务由 worker 消费，RAG 检索与内部 chat/embedding 服务分别对接。

## 当前批次边界

仅调整公开内容与确认有问题的内容加载配置；保持现有内容 schema、静态输出、路由、导航、唯一播放器与会话边界。修改加载器前核对条目 ID/调用方；不升级框架、修改 API/数据库、引入新依赖或迁移私有数据。

已核对 Astro 5.18.2 的 glob-loader：同一文件 digest 变化时，重新解析前 store 已含该 ID，会触发重复提示；当前三个集合的源文件与缓存条目为 1/1/2，没有重复条目或内容丢失。本批保留内容加载配置与依赖，单独记录该诊断行为。验证与证据回写到 [PROGRESS.md](./PROGRESS.md) 和开发进度。

## Cloudflare 公开部署

`pnpm build:pages` → PUBLIC_SITE_URL 配置门禁 → 以 PUBLIC_PRIVATE_AREA_ENABLED=false 构建 → 写入 `_redirects` / `_headers` → 原静态发布门禁 → Wrangler Direct Upload → Cloudflare Pages。只有该构建关闭登录表单；默认构建不改变认证行为。控制台路径由 Pages 302 转至 `/login/` 提示页，API 没有云端代理。

Pages 项目为 starisle，生产分支标记为 main，地址为 https://starisle-6bt.pages.dev/。上传的是当前未提交源码构建出的静态文件，不自动推送 Git，也不部署 FastAPI、SQLite、对象存储、worker 或模型。保留原 Caddy/Compose 路线供私有栈后续决策，当前不迁移数据库至 D1 或文件至 R2。
