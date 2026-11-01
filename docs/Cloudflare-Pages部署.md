# Cloudflare Pages 公开站部署

更新：2026-11-01。生产地址：[starisle-6bt.pages.dev](https://starisle-6bt.pages.dev/)。项目 `starisle`，生产分支标记 `main`，使用免费静态托管及平台分配的子域名，没有购买服务器、域名或启用付费后端资源。

## 当前覆盖范围

公开首页、信封入口、项目、简历、404、robots 和 sitemap 已上线。Pages 构建让 `/login/` 显示“私人空间暂未开放”，不渲染密码表单和登录 island；`/console` 与其子路径 302 转到该提示页。`/api/v1` 没有代理，未部署接口返回 404。

FastAPI、SQLite schema v5、文件/照片存储、邮件、知识库、worker 和模型继续在本地开发。默认 `pnpm build` 不关闭登录功能，原 Caddy/Compose 配置保留。当前没有迁移至 D1、R2 或 Pages Functions。

## 构建与发布

前置：仓库原有 pnpm/Python 依赖可用；临时 Wrangler 4.156.0 要求 Node.js 22+，本机为 24.14.1。CLI 使用单独临时 npm 缓存，不修改应用依赖或锁文件。

已存在项目的更新流程如下。在项目根目录运行，只部署 dist，命令不执行 Git 暂存、提交或推送：

```powershell
$env:npm_config_cache = Join-Path $env:TEMP 'starisle-cloudflare-npm-cache'
$env:PUBLIC_SITE_URL = 'https://starisle-6bt.pages.dev'
pnpm build:pages
if ($LASTEXITCODE -ne 0) { throw 'Pages build or release gate failed' }
npx --yes wrangler@4.156.0 pages deploy apps/web/dist --project-name starisle --branch main --commit-dirty=true
```

仅在 CLI 未认证时运行以下命令，并在浏览器内完成官方授权；不要将密码、验证码或 token 发到聊天或写入仓库：

```powershell
$env:npm_config_cache = Join-Path $env:TEMP 'starisle-cloudflare-npm-cache'
npx --yes wrangler@4.156.0 login --scopes account:read user:read pages:write
```

`build:pages` 先检查实际根 HTTPS URL，随后以 `PUBLIC_PRIVATE_AREA_ENABLED=false` 构建 11 页，生成 `_redirects` 与 `_headers`，检查提示页无登录表单，再运行原有完整发布门禁。普通本地构建不生成这两份 Pages 专用规则。发布前必须再次通过该脚本，不能将后续普通构建覆盖后的 dist 直接上传。

响应头包括 nosniff、Referrer-Policy、X-Frame-Options，以及 `/login` / `/login/*` 的 X-Robots-Tag。当前页面本身仍保留 noindex 语义。规则与两种构建均见 [构建脚本](../scripts/build_cloudflare_pages.py) 和 [登录页](../apps/web/src/pages/login.astro)。

首次创建时，Wrangler 4.156.0 默认将新 Pages 项目委托到 Workers；从 monorepo 根目录触发应用检测错误。已按 CLI 明示的方式用 `pages project create starisle --production-branch main --force` 直接创建 Pages，实际分配 `starisle-6bt.pages.dev`。项目已存在，后续部署不得再传该首次创建选项，也无需重复创建。

## 当前部署与验证

| 项目 | 结果 |
| --- | --- |
| 当前生产部署 | `a67f7bdb-7e1b-41b6-b52e-24d6c4050883`，Cloudflare MCP 返回 production / deploy / success |
| 该次部署 URL | https://a67f7bdb.starisle-6bt.pages.dev/ |
| 最终制品 fingerprint | 55 文件，SHA-256 `9e29df03aa211bae144ec555f854c3cf5de75db4ee518fd2f0286727b83f85b0` |
| 固定构建输入 inventory | 原门禁的 5 个输入文件，SHA-256 `acc416cc50d8eadfe8e6f0577774beb093973e9cf9cc32350b607ffc5adcdda3`；不是完整源码树哈希 |
| 单元和静态检查 | Web 156 项通过；Astro 105 files，0 errors / warnings / hints；构建脚本 Ruff 与浏览器脚本致命错误检查通过，浏览器脚本全量 13 项旧诊断另见专题 |
| 默认与 Pages 模式 | 默认登录通过完整隔离浏览器 E2E 12 组 / 0 失败；Pages 候选无表单且完整发布门禁通过 |
| 线上 HTTP | 19 项通过：5 公开页 200、4 尾斜杠 308、6 控制台转向 302、2 未部署/未知路径 404、robots/sitemap 200 |
| 制品比对 | 5 个公开页面原始 HTML 的 SHA-256 与本地候选一致；sitemap 仅 3 个公开收录 URL |
| Chromium 桌面/移动/无脚本 | 1440×900 / 390×844 验证信封、6 仓库及筛选/排序/点击状态、邮箱、私人提示与转向；受检页面 0 API 请求 / 0 pageerror，无横向溢出；无脚本两个链接均正常；音乐入口隐藏 |
| 当前移动 Lighthouse | 开封后主页 96/100/100/100，项目/简历均 100/100/100/100；首访进入 noindex 信封页为 98/100/100/66，带重定向警告，另行记录 |

首次上传版本为 `8a4139ad-121f-4654-8a1e-92c0b881218a`。随后补 `/login/*` 响应头并让控制台直接转 `/login/`，修正为 `8e857e1d-cae4-4b44-8dc2-c85dbc3f3fdb`。本轮来源选择器与首访规范路径修正发布为表中 a67f7bdb，首访不再经过无尾斜杠 `/enter`；原开封与 noindex 设计保留。

首次默认 Python urllib 请求被 Cloudflare 拒绝，403 / error 1010；按官方文档为检查客户端设置应用标识 `StarIsle-DeploymentCheck/1.0` 后通过，不伪装浏览器、不关闭防护。一次部署专属 URL 的 Python 请求出现 TLS 握手错误；表中的线上 HTTP 与浏览器验证均针对实际生产地址，不把该专属地址单列为访问验收通过。

初次 Pages 源码和文档已按用户确认提交并推送为 `561ef4ea59d981afeb3a1ec37e344aac1b4a004b`。之后继续开发的修正来自当前未提交工作区，部署标签仍为该 HEAD，commit_dirty=true；不能把 Source 列视为新修正已归档。制品 fingerprint、远端 HTML 和浏览器/Lighthouse 证明本次候选；后续 Git 写入继续按用户逐文件确认规则处理。

本轮上传曾遇 Authentication error 10000；第一次刷新又遇浏览器会话 CSRF 错误。经用户在默认浏览器完成相同窄权限的授权刷新后，上传和实际生产查询均成功。失败日志保留，不把“账号能读项目”视作上传权限已经有效。

## Agent 环境

按用户指定的 [Cloudflare 官方 agent-setup](https://developers.cloudflare.com/agent-setup/prompt.md) 安装了全部 19 个官方技能，位置为用户 `C:\Users\ASUS\.agents\skills`。Cloudflare MCP 已加入用户 `C:\Users\ASUS\.codex\config.toml`，URL 为 https://mcp.cloudflare.com/mcp，OAuth 成功，当前会话已加载并实际调用 whoami / Pages 查询。配置前备份保存在本机临时目录，没有复制进仓库。

MCP 授权请求限制为账号/用户读取及 Pages 读取、写入、元数据读取；未沿用首次自动发现的全部产品权限。首次 OAuth 等待超时，重新发起并由用户完成后成功。当前已实际加载，无需为了本次发布中断会话；后续会话未显示工具时可重启 Codex。可选 beta `cf` CLI 未安装，本次使用官方 Wrangler Direct Upload。

## 后续更新与回滚

在 Cloudflare 控制台打开项目 starisle 的 Deployments，核对要恢复版本的时间与部署 ID 后，使用平台的回滚功能。回滚只恢复静态站版本，不涉及本地数据库或私有文件；本次没有执行回滚。后续若选择 Git 自动部署，先核对 Pages Direct Upload 与 Git 项目切换限制，再按单独批准的 Git 文件清单处理，不自动创建远端提交。

当前 Lighthouse 已复测并保留原始报告。用户确认首发音乐暂时隐藏，播放/续播验收后置；Safari/iOS/人工读屏、国内不同网络、首访 SEO 策略和真实模型/私有栈仍需独立验收，M1/M2/M3 未全部完成。详细证据见 [Lighthouse 与筛选修复](./2026-11-01_公开站Lighthouse与筛选修复.md)。

## Evidence → Finding → Path

| Evidence | Finding | Path |
| --- | --- | --- |
| 实际 build:pages、5 页线上 HTML 哈希与当前 Lighthouse | 当前公开静态候选已发布并完成实验室评估 | 保留首访 SEO / 真实设备边界，音乐按用户决定后置 |
| Cloudflare MCP 的 production / success 及线上 19 项 HTTP | 项目发布和路由行为已有直接证据 | 保留公开站，私有栈继续按原阶段独立落实 |
| 默认构建和 Pages 构建登录页面检查 | 云端无需后端即可给出清楚提示，本地认证能力保留 | 获得私有栈资源后另行设计同源接入 |

官方参考：[Astro 与 Pages](https://developers.cloudflare.com/pages/framework-guides/deploy-an-astro-site/)、[Direct Upload](https://developers.cloudflare.com/pages/get-started/direct-upload/)、[重定向](https://developers.cloudflare.com/pages/configuration/redirects/)、[响应头](https://developers.cloudflare.com/pages/configuration/headers/)、[1010 客户端检查](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1010/)。
