# 公开站 Lighthouse 与仓库筛选修复

更新：2026-11-01。本批在用户确认的 Git 发布后继续开发；新修正已通过本地检查并发布为 `a67f7bdb-7e1b-41b6-b52e-24d6c4050883`，完成最终线上 Lighthouse、HTTP 和交互复验。后续开发改动尚未再次提交。

## Git 发布已完成

用户逐文件确认 21 个待提交文件，并同意随推送上传 3 个既有未推送提交。先读取 `.gitignore`，扫描本次提交与既有提交共 28 个不同路径，核对忽略规则、真实凭据、文件类型和大小；两处启发式命中分别是环境变量名及 HEAD 已有的隔离测试签名值，不是真实运行凭据。

提交为 `561ef4ea59d981afeb3a1ec37e344aac1b4a004b`，说明为 `feat: publish public site on Cloudflare Pages and refresh project progress`，已推送 GitHub origin/main。本地 HEAD、跟踪分支及实际远端哈希一致，发布完成时工作区干净。后续开发产生的新改动不属于该提交。

本机审阅清单、逐操作日志和结果分别位于 `logs/git-publication-20261101/allowlist.md`、`operations.jsonl`、`publication-result.json`；这些证据受既有忽略规则保护，不加入 Git。提交文件清单也可在 [GitHub 提交](https://github.com/xiaominaimoyu/StarIsle/commit/561ef4ea59d981afeb3a1ec37e344aac1b4a004b)查看。

## 已部署版本的移动 Lighthouse 基线

使用 Lighthouse 13.6.0 和本机 Playwright Chromium，四项按性能 / 无障碍 / 最佳实践 / SEO 排列。这是本机网络和浏览器的单次实验室样本，不是真实设备验收或 CrUX 现场指标。

| 实际页面与场景 | 性能 | 无障碍 | 最佳实践 | SEO | 报告 |
| --- | --- | --- | --- | --- | --- |
| 首页，实际开封后再冷缓存导航 | 96 | 100 | 100 | 100 | home-after-entry-baseline.report.json |
| 项目页 | 99 | 94 | 100 | 100 | projects-baseline.report.json |
| 简历页 | 99 | 100 | 100 | 100 | resume-baseline.report.json |
| 首次请求 `/`，最终进入 `/enter/` | 98 | 100 | 100 | 66 | home-first-visit-baseline.report.json |

默认移动 profile 为 412×823、DPR 1.75、模拟 RTT 150ms / 1638.4 Kbps / CPU 4 倍。首页开封后场景通过真实键盘操作进入主页，保留页面实际写入的 sessionStorage，再显式清空 HTTP 缓存；报告中 disableStorageReset=true，区别于其余默认清空存储的样本。未绕过开封事件或修改生产入口以获取分数。

首次 `/` 会按现有设计跳到刻意 noindex 的 `/enter/`，该报告带重定向警告，SEO 66 来自禁止收录。没有移除 noindex、屏蔽该审计或把开封后首页得分冒充首访得分。该首访行为与长期公开 SEO 策略仍须单独判断，不能据此宣称全部公开入口 SEO 已达标。

## 修正后实际生产版本

Cloudflare MCP 确认生产部署 `a67f7bdb-7e1b-41b6-b52e-24d6c4050883` 为 success。生产地址仍为 https://starisle-6bt.pages.dev/，新候选 55 文件 fingerprint 为 `9e29df03aa211bae144ec555f854c3cf5de75db4ee518fd2f0286727b83f85b0`。同一 Lighthouse 版本/profile 下，最终线上报告为：

| 实际页面与场景 | 性能 | 无障碍 | 最佳实践 | SEO | LCP / TBT / CLS |
| --- | --- | --- | --- | --- | --- |
| 首页，真实开封后再加载 | 96 | 100 | 100 | 100 | 988ms / 236ms / 0 |
| 项目页 | 100 | 100 | 100 | 100 | 1055ms / 0ms / 0 |
| 简历页 | 100 | 100 | 100 | 100 | 998ms / 0ms / 0 |
| 首次 `/`，最终进入 `/enter/` | 98 | 100 | 100 | 66 | 2430ms / 0ms / 0 |

项目页无障碍从 94 到 100，与修复的六个不适用 ARIA 节点及线上 DOM 检查一致。性能 99 到 100 属于单次样本波动范围，不把它归因于选择器修复。未降低 CPU/网络模拟、不挑选重复运行中的最高分。三份公开内容页报告无 runtimeError / runWarnings；首访报告保留跳转警告和 noindex 结果。没有现场 INP 或真实用户 CrUX 样本。

项目/简历/开封后主页报告无 runtimeError 或 runWarnings；CLI 日志另有 `Interactive timestamp not found` 诊断，原日志保留。没有用报告摘要掩盖首次跳转警告。

## 根据实际失败修改

项目页 Lighthouse 报告定位到六个 `<li data-repo-card>` 被写入 `aria-pressed`。组件的来源控件选择器 `[data-repo-source]` 同时命中了来源按钮和使用同一数据属性的列表项，导致列表项被加上按钮状态/样式，点击仓库链接还会冒泡触发来源筛选。

修复只将选择器收窄为 `button[data-repo-source]`，不修改数据、排序规则或链接导航。新增 DOM 回归直接执行组件的真实客户端脚本：一项验证筛选/排序期间仅按钮拥有 pressed 状态，另一项验证点击仓库链接不会改变来源。两项在修复前均按预期失败，修复后通过。

首访报告还识别到 `/` → `/enter` → `/enter/` 的跳转链。主页现在直接跳到 `/enter/`，减少一次平台补尾斜杠请求，保留首访开封、完成标记、循环保护与 noindex 设计。隔离 E2E 的三处路径断言按真实 URL path 统一处理可选尾斜杠，继续验证原有入口条件。

| 本批应用与测试文件 | 变更 |
| --- | --- |
| apps/web/src/components/repos/PublicRepoGrid.astro | 一行按钮选择器修正 |
| apps/web/src/components/repos/public-repo-grid.test.ts | 两项真实客户端脚本 DOM 回归 |
| apps/web/src/pages/index.astro | 直接导航规范化 `/enter/` |
| scripts/browser_e2e.py | 三处入口断言容忍规范化尾斜杠，不删除条件 |

## 修正候选的本地验证

| 检查 | 结果 |
| --- | --- |
| Web Vitest | 21 files / 156 passed，含两项新增回归 |
| Astro 检查 | 105 files，0 errors / warnings / hints |
| 完整隔离 browser_e2e.py | 12 组、0 失败；隔离邮件原文归档校验通过，exit 0 |
| harness 安全测试 | 31 passed |
| Python 致命错误检查 | Ruff E9/F63/F7/F82 通过 |
| Ruff 全量对比 | 当前和 561ef4e 的 browser_e2e.py 均为相同 13 项既有诊断，0 新增；没有把全量 Ruff 声称为零问题 |
| Pages 发布门禁 | 实际 URL 下完整通过；55 文件 fingerprint `9e29df03aa211bae144ec555f854c3cf5de75db4ee518fd2f0286727b83f85b0` |

完整浏览器回归仍是自有隔离 loopback API / worker / Astro / Chromium；其邮件、RAG 和恢复检查不能代替真实云端私有栈、模型或生产邮件验收。本批没有启动模型、读取真实私有数据、改 API/schema、升级应用依赖或修改 `.gitignore`。

## 线上复验与授权问题处理

候选上传在 Cloudflare `/pages/projects/starisle/upload-token` 返回 `Authentication error [code: 10000]`，命令 exit 1。MCP 读取确认旧生产部署仍为 success。没有重试未更改的上传命令，也未扩大到其他产品权限；按官方流程发起相同账号读取 / 用户读取 / Pages 发布授权的刷新。

首次刷新显示 Cloudflare“请验证您是真人”，随后返回 `request_forbidden: No CSRF value available in the session cookie`。重新使用 Wrangler 的默认浏览器入口建立会话，用户完成验证与授权后 CLI 返回 Successfully logged in。沿用相同窄权限上传成功，没有关闭安全验证或改用其他账户。

最终线上复验包括 19 项 HTTP 检查、5 个页面 HTML 与本地 SHA-256 一致、桌面 1440×900 / 移动 390×844 浏览器测试。仓库卡片数量为 6、无不适用的 aria-pressed；Gitee 空态、恢复全部、时间排序与真实点击外部仓库链接后的筛选状态正确。首次文档请求仅为 `/` 200 → `/enter/` 200，未请求无尾斜杠 `/enter`。无 JavaScript 的两个入口仍正常；受检本站页面 0 API 请求 / 0 pageerror。

用户已明确“首发暂时隐藏音乐功能”。本批线上核对音乐入口均隐藏，授权曲目验收后置到后续开放批次；这不是播放器播放/续播已通过。M1/M2/M3 仍未全部验收，首访 SEO 策略与真实设备等门槛保留。

Lighthouse 阶段检查时，Docker CLI / Compose 5.6.0 可用，Linux 引擎管道不可用。本阶段先执行既有合成部署预检，四种配置共 89 项检查通过，只解析 config。随后继续恢复引擎并完成一次实际隔离容器/Caddy 基础联调，独立证据见 [Docker-Caddy 隔离联调](./2026-11-01_Docker-Caddy隔离联调.md)，不将该阶段的离线预检混为实际运行。

## 本机证据与复查

原始 HTML/JSON、trace、devtools log、命令日志和 SHA-256 inventory 位于 `logs/web-perf-20261101-561ef4e/`，基线及最终共 8 份报告、32 个原始资产文件，索引为 `audit-manifest.json`；线上交互/HTTP 结果为 pages-final-browser.json / pages-final-http.json。该目录由既有 logs 忽略规则保护，不上传原始报告或输出产物。

CLI 报告使用以下命令模式生成，在仓库根目录运行；更换输出名以保留已有证据：

```powershell
$env:CHROME_PATH = Join-Path $env:LOCALAPPDATA 'ms-playwright\chromium-1234\chrome-win64\chrome.exe'
$env:npm_config_cache = Join-Path $env:TEMP 'starisle-lighthouse-npm-cache'
npx --yes lighthouse@13.6.0 https://starisle-6bt.pages.dev/projects/ --chrome-flags='--headless=new' --only-categories=performance,accessibility,best-practices,seo --output=json --output=html --output-path=logs/web-perf-20261101-561ef4e/projects-recheck --save-assets --no-enable-error-reporting
```

Chrome 路径为本机已核对的版本位置；其他机器应使用其实际浏览器位置。开封后主页采用同目录的 `audit-home-after-entry.mjs`，由真实开封导航建立状态、清空 HTTP 缓存，再使用 Lighthouse 提供的 Page 参数审计同一页面。

DevTools MCP 另采集了项目页 390×844、Slow 4G / CPU 4 倍的观察值：LCP 2099ms、CLS 0、TTFB 158ms。工具拒绝显式 trace 输出路径，两次路径请求均未写文件；随后仅保留了返回的诊断摘要。这组观察没有可导出的原始 trace，只用于定位，不替代上述独立 Lighthouse 原始报告。

## Evidence → Finding → Path

| Evidence | Finding | Path |
| --- | --- | --- |
| 原部署项目页 Lighthouse 的 aria-allowed-attr 失败节点、两项 DOM 回归 | 来源选择器跨越了按钮与列表项 | 收窄真实按钮选择器，保留数据与链接行为，候选发布后复验 |
| 四份移动 Lighthouse 报告与首次跳转警告 | 内容页达目标，首访进入 noindex 页面；两种场景得分必须分开 | 保留真实入口行为，减少多余跳转，不通过去掉 noindex 提高分数 |
| 新候选完整门禁、授权刷新、MCP success 与 19 项 HTTP / 浏览器检查 | 新修正已实际发布并复验，后续开发源码尚未再次提交 | 保留最终候选及原始报告，后续 Git 写入继续单独确认精确范围 |

官方参考：[Lighthouse 评分与波动](https://developer.chrome.com/docs/lighthouse/performance/performance-scoring)、[Wrangler 登录与授权](https://developers.cloudflare.com/workers/wrangler/commands/general/)。
