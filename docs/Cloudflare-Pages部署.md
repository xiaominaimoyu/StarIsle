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
| 当前生产部署 | `8e857e1d-cae4-4b44-8dc2-c85dbc3f3fdb`，Cloudflare MCP 返回 production / deploy / success |
| 该次部署 URL | https://8e857e1d.starisle-6bt.pages.dev/ |
| 最终制品 fingerprint | 55 文件，SHA-256 `b423783cbf2680f75832202e58c2f52f17a182feca08fa816a2367abdf54afad` |
| 固定构建输入 inventory | 原门禁的 5 个输入文件，SHA-256 `acc416cc50d8eadfe8e6f0577774beb093973e9cf9cc32350b607ffc5adcdda3`；不是完整源码树哈希 |
| 单元和静态检查 | Web 154 项通过；Astro 104 files，0 errors / warnings / hints；新增脚本 Ruff 通过 |
| 两种构建 | 默认构建保留真实 LoginForm 与表单；Pages 构建无表单且完整发布门禁通过 |
| 线上 HTTP | 19 项通过：5 公开页 200、4 尾斜杠 308、6 控制台转向 302、2 未部署/未知路径 404、robots/sitemap 200 |
| 制品比对 | 5 个公开页面原始 HTML 的 SHA-256 与本地候选一致；sitemap 仅 3 个公开收录 URL |
| Chromium 桌面/移动 | 桌面 1440×900 验证信封可见与键盘进入、6 个仓库、邮箱、私人提示与转向，受检路径 0 API 请求 / 0 pageerror；移动 390×844 验证信封键盘入口、主页和私人提示，受检页面无横向溢出 |

首次上传版本为 `8a4139ad-121f-4654-8a1e-92c0b881218a`。线上观察到 Pages 自动补尾斜杠，而最初 `_headers` 只匹配 `/login`；随后补 `/login/*` 并让控制台直接转 `/login/`，发布为表中最终版本。没有降低 noindex 门槛或移除失败检查。

首次默认 Python urllib 请求被 Cloudflare 拒绝，403 / error 1010；按官方文档为检查客户端设置应用标识 `StarIsle-DeploymentCheck/1.0` 后通过，不伪装浏览器、不关闭防护。一次部署专属 URL 的 Python 请求出现 TLS 握手错误；表中的线上 HTTP 与浏览器验证均针对实际生产地址，不把该专属地址单列为访问验收通过。

部署元数据仍显示原 HEAD `c07e8f1`，同时 `commit_dirty=true`；本次实际上传来自未提交工作区，不能把远端 Source 列视为完整源码已归档。制品 fingerprint 和远端 HTML 比对证明的是本次候选及受检文件。Git 发布继续按用户逐文件确认规则单独处理。

## Agent 环境

按用户指定的 [Cloudflare 官方 agent-setup](https://developers.cloudflare.com/agent-setup/prompt.md) 安装了全部 19 个官方技能，位置为用户 `C:\Users\ASUS\.agents\skills`。Cloudflare MCP 已加入用户 `C:\Users\ASUS\.codex\config.toml`，URL 为 https://mcp.cloudflare.com/mcp，OAuth 成功，当前会话已加载并实际调用 whoami / Pages 查询。配置前备份保存在本机临时目录，没有复制进仓库。

MCP 授权请求限制为账号/用户读取及 Pages 读取、写入、元数据读取；未沿用首次自动发现的全部产品权限。首次 OAuth 等待超时，重新发起并由用户完成后成功。当前已实际加载，无需为了本次发布中断会话；后续会话未显示工具时可重启 Codex。可选 beta `cf` CLI 未安装，本次使用官方 Wrangler Direct Upload。

## 后续更新与回滚

在 Cloudflare 控制台打开项目 starisle 的 Deployments，核对要恢复版本的时间与部署 ID 后，使用平台的回滚功能。回滚只恢复静态站版本，不涉及本地数据库或私有文件；本次没有执行回滚。后续若选择 Git 自动部署，先核对 Pages Direct Upload 与 Git 项目切换限制，再按单独批准的 Git 文件清单处理，不自动创建远端提交。

当前未验证 Lighthouse、Safari/iOS/人工读屏、国内不同网络、授权音频和真实模型/私有栈；M1/M2/M3 不因公开站上线而完成。

## Evidence → Finding → Path

| Evidence | Finding | Path |
| --- | --- | --- |
| 实际 build:pages 输出及 5 页线上 HTML 哈希 | 公开静态候选已通过现有门禁并实际发布 | 后续运行当前 Lighthouse 与补音频/设备证据 |
| Cloudflare MCP 的 production / success 及线上 19 项 HTTP | 项目发布和路由行为已有直接证据 | 保留公开站，私有栈继续按原阶段独立落实 |
| 默认构建和 Pages 构建登录页面检查 | 云端无需后端即可给出清楚提示，本地认证能力保留 | 获得私有栈资源后另行设计同源接入 |

官方参考：[Astro 与 Pages](https://developers.cloudflare.com/pages/framework-guides/deploy-an-astro-site/)、[Direct Upload](https://developers.cloudflare.com/pages/get-started/direct-upload/)、[重定向](https://developers.cloudflare.com/pages/configuration/redirects/)、[响应头](https://developers.cloudflare.com/pages/configuration/headers/)、[1010 客户端检查](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1010/)。
