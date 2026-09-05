# 星屿 StarIsle - 前端开发计划

> **Planned / Not implemented。** 本文的目录、组件、路由、接口、命令和验收均为后续开发约定。当前仓库尚未包含 `apps/web` 或任何应用源码，不能把本文写成现状或已执行结果。

> **文档关系与优先级：** [总体具体开发计划](./星屿%20StarIsle%20-%20总体具体开发计划.md)是跨模块最高执行基线；[高层总体规划](./星屿%20StarIsle.md)说明愿景与范围。本文只细化前端，并在冲突时服从总体具体开发计划。

版本：v1.1  
编制日期：2026-08-27  
文档性质：前端专项实施计划  
适用范围：规划中的 `apps/web` 及其与 `packages/contracts` 的契约关系

## 1. 前端目标与阶段边界

前端承担公开个人门户与私有控制台的浏览器体验。公开页面使用 Astro 静态输出；背景、播放器、登录表单、相册、文件、收件箱和知识库使用按需加载的 React Islands。前四项的正确描述是“无常驻后端依赖的静态部署”，而不是“纯前端”：它们仍包含必要的浏览器端 JavaScript 和构建期数据流程。

| 阶段 | 时间 | 前端交付目标 | 后端依赖 | 前端验收 |
| --- | --- | --- | --- | --- |
| 一期 | 2026-09-01 至 09-13 | 求职展示、仓库快照、背景、播放器、SEO、单一静态托管 | 无常驻后端依赖 | 移动端 Lighthouse 四项均不低于 90 |
| 二期 | 2026-09-14 至 10-11 | 登录、控制台、相册、文件、收件箱、同源 API 适配 | `/api/v1` 的 auth/files/photos/mail | 未授权体验正确且私有 API 实际拒绝未授权请求 |
| 三期 | 2026-10-12 至 11-08 | 知识库文档管理、作业状态、会话与 RAG 流式问答 | `/api/v1` 的 knowledge/jobs/conversations/rag | POST 流可展示引用、错误、心跳、完成与取消 |

### 1.1 前端的不可变约束

- 公共页面不得携带 secret、私有对象路径、Cookie 内容或私人数据；`PUBLIC_*` 环境变量一律视为可被浏览器读取。

- API 默认同源，`PUBLIC_API_BASE` 的规划默认值为 `/api/v1`。前端只维护 session 状态，绝不将 JWT 存入 `localStorage`、`sessionStorage` 或 IndexedDB。

- 真实鉴权以后端为准。路由守卫只改善体验，不能把 UI 隐藏当作授权控制。

- 一期默认 Cloudflare Pages；账户条件不满足时唯一替换为 GitHub Pages。Vercel 不作为第三套部署方案，也不并行维护两套生产静态站点。

- 每周约 20 小时；少于 15 小时时先延期增强项，不缩减可访问性、隐私、授权媒体、Cookie/CSRF 协同或阶段门禁。

## 2. 技术决策与替代边界

| 类别 | 规划选择 | 用途 | 替代或边界 |
| --- | --- | --- | --- |
| 框架 | Astro 5+ | 静态生成、内容页、路由和 Islands 宿主 | 不将整个门户变成单页应用 |
| 交互岛 | React 19 | 播放器、背景、控制台、相册、收件箱、聊天 | 只在需交互处水合 |
| 样式 | Tailwind CSS 4，CSS-first | 设计令牌、主题、响应式布局 | `tailwind.config.*` 不是必需规划路径；以 CSS 配置为主 |
| 语言 | TypeScript strict | 组件、API 类型与状态约束 | 共享契约来自 `packages/contracts`，不复制多份定义 |
| 跨岛状态 | nanostores | 主题、播放器、背景与 session 摘要 | 不存放 JWT 或可恢复凭据 |
| 本地存储 | IndexedDB + Dexie | 背景 Blob 与仅浏览器侧偏好 | 背景仅同设备同浏览器持久化 |
| 音频 | HTML5 Audio | 播放、进度、音量与队列 | 一期核心为进度续播，不依赖第三方播放器 |
| 构建期仓库数据 | `scripts/fetch-repos.ts` 规划脚本 | GitHub/Gitee 分页抓取、归一化、版本化 JSON 缓存 | 运行时不请求外网；Token 只在 CI/服务端构建环境 |
| Mock 与测试 | MSW、Vitest、Playwright | 契约先行、逻辑测试与关键端到端路径 | 所有命令均待源码就位后执行 |
| HTML 清洗 | DOMPurify | 邮件 HTML 和不可信渲染内容的浏览器侧防御层 | 服务端清洗仍是主边界；默认阻止远程图片 |
| 组件文档 | 组件示例页 | MVP 中可验证交互状态 | Storybook 是 Could，未达容量不引入 |

React 19 Islands 的价值在于将静态展示和中等复杂度交互拆开。后续若某个岛需要更小包体或不同实现，可以独立评估；这不是一期迁移计划，也不能改变对外 URL、共享契约或阶段门禁。

## 3. 规划目录与渲染策略

### 3.1 规划目录树（尚未创建）

```text
star-isle/
├── apps/
│   └── web/
│       ├── public/
│       │   ├── music/                   # 授权媒体与封面
│       │   ├── presets/backgrounds/     # 预设背景
│       │   └── resume.pdf               # 经确认后才放入的公开简历
│       ├── src/
│       │   ├── content/                 # about、projects、resume 等内容源
│       │   ├── content.config.ts         # Astro Content Collections 定义
│       │   ├── data/                     # 构建期仓库 JSON 与播放列表
│       │   ├── layouts/                  # Base、Portal、Console 布局
│       │   ├── pages/                    # 页面路由
│       │   ├── components/               # ui、site、repos、player、background、auth、gallery、files、mail、kb
│       │   ├── stores/                   # player、theme、background、session 摘要
│       │   ├── lib/
│       │   │   ├── api/                  # 同源 fetch 客户端与模块调用
│       │   │   ├── auth.ts                # session 与 CSRF 辅助；不存取 JWT
│       │   │   ├── repos.ts              # 统一仓库 schema 与 featured 规则
│       │   │   ├── db.ts                 # Dexie 背景 Blob
│       │   │   ├── image.ts              # 浏览器图片压缩与方向读取
│       │   │   └── format.ts             # 日期和字节格式化
│       │   └── styles/                   # CSS-first Tailwind 与全局令牌
│       └── astro.config.mjs
├── packages/
│   └── contracts/                         # 前后端共享请求、响应、错误与流事件类型
├── scripts/
│   └── fetch-repos.ts                     # 规划中的构建期快照工具
└── tests/                                 # 前端、契约和端到端测试
```

所有上列路径都是计划，不意味着当前目录或文件存在。`data/` 运行时内容与 secret 不属于前端源码提交物；实际忽略规则在后续首个仓库基础提交时审查。

### 3.2 渲染与水合策略

| 页面或组件 | 规划策略 | 原因与降级 |
| --- | --- | --- |
| 首页、项目、简历、404 | Astro SSG，尽可能少的 JS | SEO 和性能优先；公开内容可静态发布 |
| 背景设置、播放器 | `client:idle` Islands | 不阻塞首屏；不支持时使用预设背景与基本播放器控件 |
| 登录 | 静态壳 + `client:load` 表单 | 需要立即响应表单与错误状态 |
| 控制台、相册、文件、邮件、知识库 | 静态壳 + `client:load` Island | 私有数据仅在运行时同源 API 获取 |
| 播放器跨页体验 | 默认保存并恢复进度 | `ClientRouter` / `transition:persist` 保活仅为增强，失败不阻塞 M1 |

## 4. 路由、会话与 API 数据层

### 4.1 规划路由

| 路由 | 阶段 | 可见性 | 页面责任 |
| --- | --- | --- | --- |
| `/` | 一期 | 公开 | Hero、关于我、技能、精选项目、模块入口 |
| `/projects` | 一期 | 公开 | 项目内容与 GitHub/Gitee 仓库快照 |
| `/resume` | 一期 | 公开 | 在线简历与确认后的 PDF 下载 |
| `/login` | 二期 | 公开 | 单用户登录与受控 `redirect` 参数 |
| `/console` | 二期 | 登录后 | 用量、最近照片、最新邮件和作业摘要 |
| `/console/gallery` | 二期 | 登录后 | 相册、缩略图、时间轴、上传与删除 |
| `/console/files` | 二期 | 登录后 | 文件列表、上传、下载与删除 |
| `/console/mail` | 二期 | 登录后 | 邮件列表、阅读与已读状态 |
| `/console/kb` | 三期 | 登录后 | 文档列表、上传、删除、重建、作业与会话问答 |
| `/404` | 一期 | 公开 | 可访问的品牌化错误页 |

未登录访问 `/console/*` 时，前端可以跳转到 `/login?redirect=...`，但 API 返回的 401/403 才是授权判断。`redirect` 必须限制为站内相对路径，避免开放重定向。

### 4.2 Session 状态与 CSRF

`src/lib/api/client.ts` 规划为唯一 fetch 入口：默认 `credentials: "include"`，base URL 默认 `/api/v1`，将 API 错误规范化为 `ApiError`，并把 401/403 交给 session store 处理。`src/lib/auth.ts` 只维护“是否已认证、用户摘要、CSRF 是否已获取”等 session 状态，不读取、存取或保存 JWT。

前端在首次需要写操作前请求 `GET /api/v1/auth/csrf`，将返回的会话绑定 token 用于本次会话的 `X-CSRF-Token` 请求头。登录、刷新、退出、上传、删除、标记已读、文档入库和重建必须携带该头；不能只依赖 `X-Requested-With`。浏览器同源默认，若将来改变域名拓扑，必须先由总体具体计划更新 CORS/Origin 规则。

### 4.3 API 调用边界

| 模块 | 规划端点 | 前端责任 |
| --- | --- | --- |
| 鉴权 | `GET /auth/csrf`、`POST /auth/login`、`POST /auth/refresh`、`POST /auth/logout`、`GET /auth/me` | session 摘要、错误/限速提示、站内跳转；不管理 token |
| 文件 | `GET/POST /files`、`GET /files/{id}/download`、`DELETE /files/{id}` | cursor 分页、进度、失败反馈、确认删除 |
| 照片 | `GET/POST /photos`、`DELETE /photos/{id}` | 缩略图时间轴、批量上传进度、GPS 不默认显示 |
| 邮件 | `GET /mail`、`GET /mail/{id}`、`POST /mail/{id}/read` | 纯文本优先、清洗后的 HTML 容器、默认禁远程图片 |
| 知识库 | `GET/POST /knowledge/documents`、`DELETE /knowledge/documents/{id}`、`POST /knowledge/documents/{id}/rebuild` | 文档列表、上传、删除、重建和 job 状态 |
| 作业 | `GET /jobs/{id}` | 状态、进度、可解释失败码与轮询退避 |
| 会话 | `GET/POST /conversations`、`GET /conversations/{id}` | 会话列表、历史加载与引用关联 |
| RAG | `POST /rag/query` | 以 fetch 读取 POST 流、取消、引用、心跳、错误和完成状态 |

`packages/contracts` 应定义前后端共同使用的 `ApiError`、cursor 分页、资源摘要、作业状态、流事件和引用对象；前端不维护独立的“猜测版”后端类型。首份契约测试通过前，文档中的字段仅为计划草案。

## 5. 设计系统与性能预算

### 5.1 视觉令牌

星屿的视觉方向是“深邃夜空与岛屿灯火”：默认暗色优先，亮色为辅助，主色为星蓝，点缀为暖金。主题与自定义背景正交：背景在最底层，内容层永远有可调 scrim，确保任何背景下文字可读。

```css
/* apps/web/src/styles/global.css 的规划令牌；源码就位后实现并验证 */
@theme {
  --color-ink: #0b1020;
  --color-ink-soft: #141b33;
  --color-star: #7aa2ff;
  --color-glow: #ffd479;
  --color-paper: #f7f7fb;
  --font-display: "Noto Serif SC", serif;
  --font-body: "Inter", "Noto Sans SC", sans-serif;
  --radius-card: 1rem;
}
```

- 主题以 `data-theme="dark|light"` 应用在 `<html>`；防闪烁脚本只恢复主题偏好，背景 Blob 允许在首屏后淡入，避免同步读取 IndexedDB 阻塞渲染。

- 卡片采用 4 px 间距基网格、最多 72 rem 页面宽度和 48 rem 内容栏。动画使用 150 至 250 ms `ease-out`，并遵循 `prefers-reduced-motion`。

- 所有可交互元素可键盘到达并有可见焦点环；文本与背景的对比至少达到 4.5:1。

### 5.2 性能与 SEO 预算

| 指标 | 规划预算 | 手段 |
| --- | --- | --- |
| Lighthouse 移动端 | Performance、SEO、Accessibility、Best Practices 均不低于 90 | 静态输出、按需 Islands、图片和字体预算 |
| 公开页首屏 JS | 不超过 60 KB gzip 的目标预算 | `client:idle`、无全站框架运行时、避免重型图表库 |
| LCP | Fast 4G 条件下小于 2.5 秒的目标 | 图片尺寸、`astro:assets`、高优先级 Hero 资源 |
| 图片 | 响应式、WebP/AVIF 优先 | 预留尺寸、延迟加载、必要时降级为静态封面 |
| 背景 Blob | 压缩后不超过 1.5 MB 的目标 | 选择压缩质量或提示重选；不保存超大原图 |
| 字体 | 子集化与 `font-display: swap` | 根据公开内容控制字符集，减少阻塞 |

公开页应提供 `title`、`description`、canonical、OG/Twitter 卡片、sitemap、robots 和首页 `Person`/`WebSite` 结构化数据。`/console/*` 不进入公开索引。性能目标需要在源码就位后由真实测试环境测量，不能提前宣称达标。

## 6. 一期模块方案

### 6.1 个人展示与求职页

- 使用 Astro Content Collections 管理 `about`、`projects`、`resume` 等 Markdown 内容；`src/content.config.ts` 负责 schema，不把个人资料散落到组件常量。

- 首页包含 Hero、关于我、技能、精选项目和模块入口；项目页把内容集合与仓库卡片放在清晰边界内；简历页提供在线阅读与经确认后的 PDF 下载。

- SEO、打印样式和 404 属于 M1；星空粒子、复杂动效和更多主题属于增强项，不应挤占可访问性或内容准确性。

### 6.2 仓库展示

构建期数据流为：`scripts/fetch-repos.ts` 规划脚本 → GitHub REST `/users/{username}/repos` 和 Gitee OpenAPI 的分页请求 → 统一 schema → 版本化 JSON 缓存 → Astro 静态页面。GitHub 使用分页与 `per_page=100`；Gitee 也按其分页规则读取。运行时不依赖外网 API。

```ts
// apps/web/src/lib/repos.ts 的规划契约；非当前源码
export interface Repo {
  source: "github" | "gitee";
  name: string;
  description: string | null;
  url: string;
  language: string | null;
  stars: number;
  forks: number;
  updatedAt: string;
  featured: boolean;
  topics: string[];
}
```

- `featured` 是本地配置清单决定的展示字段，不能称为 GitHub Profile pinned。

- 抓取失败时允许使用最近一次成功 JSON 缓存，并在 UI 显示来源和采集时间；不得伪装为实时数据或阻断公开页面构建。

- `GITHUB_TOKEN` 仅属于 CI 或服务端构建环境，不可进入 `PUBLIC_*`、静态 JSON 或浏览器 bundle。语言占比等额外调用为 Could。

### 6.3 自定义背景

```text
选择图片 → 浏览器内压缩 → WebP Blob 写入 IndexedDB/Dexie
        → nanostores 通知 BackgroundLayer → 背景 + scrim 渲染
```

- 初始策略为最长边 2560 px、WebP 质量约 0.85，并在源码就位后按性能预算验证。原图超过 15 MB 时拒绝并提示重选；最终 Blob 的目标是小于 1.5 MB。

- 压缩或编码失败时，界面必须提示重选图片或降低质量；不回退保存超大原图。

- 选择器提供预设背景、透明度、模糊度与恢复默认。IndexedDB 不可用时回退到预设背景，并清楚说明不能本地持久化。

### 6.4 音乐播放器

- 媒体和播放列表均为一期静态资源，且只能使用自有、授权或公版样例。

- `MusicPlayer` 使用 HTML5 Audio 和 nanostores 管理 `currentTrack`、`isPlaying`、`queue`、`progress`、`volume`；底部迷你条与歌单面板共享同一状态。

- 一期核心验收是进度续播、播放/暂停、切歌、进度和音量。`ClientRouter` 或 `transition:persist` 的跨页保活仅是增强，若验证不稳定，保持“新页恢复进度”的降级即可。

- 自动播放限制、媒体加载失败、键盘焦点与输入框冲突必须有可解释的降级，不让播放器阻塞 M1。

## 7. 二期控制台方案

### 7.1 登录与 session 体验

`LoginForm` 使用同源 `POST /api/v1/auth/login`，由浏览器接收 HttpOnly Cookie。登录成功后，前端通过 `GET /api/v1/auth/me` 建立 session 摘要；401 显示重新登录，429 显示冷却提示，不尝试读取或写入 JWT。

`ConsoleLayout` 在挂载时检查 session 状态。前端跳转仅改善流程；真实文件、照片、邮件和 RAG 接口必须独立返回 401/403。所有状态变更请求从 CSRF 客户端读取会话绑定 token 并携带 `X-CSRF-Token`。

### 7.2 相册与文件

- 相册使用缩略图网格和 EXIF 时间分组。后端只返回被允许展示的摘要；GPS 默认不展示，原件 URL 不能包含物理路径。

- 上传支持多选、拖放、单项进度和并发不高于 3。前端可做类型与大小的早期反馈，服务端校验仍为决定性安全边界。

- 文件表格包含名称、大小、类型、上传时间、下载和删除。删除需二次确认；失败不能让 UI 假装对象已经移除。

- 单月照片超过 200 张时才评估虚拟滚动；在此之前优先保证缩略图尺寸、懒加载和移动端布局。

### 7.3 网页收件箱

- 列表/阅读视图在桌面可使用双栏，在移动端折叠为单栏；初期使用 60 秒轮询或手动刷新，不引入 WebSocket。

- 正文默认显示纯文本。已清洗 HTML 仅在受限容器中渲染，DOMPurify 是浏览器侧防御层，远程图片默认不加载；任何脚本、表单和未经允许的外部资源都不被信任。

- 未读状态可乐观更新，但必须在 API 失败时回滚并显示可行动错误。邮件发送不在前端范围。

## 8. 三期知识库与流式交互

### 8.1 文档管理与作业状态

`/console/kb` 应同时提供文档列表、上传、删除、重建和作业状态。前端调用 `GET/POST /api/v1/knowledge/documents`、`DELETE /api/v1/knowledge/documents/{id}`、`POST /api/v1/knowledge/documents/{id}/rebuild`、`GET /api/v1/jobs/{id}`，显示明确的 `queued/running/succeeded/failed/cancelled` 状态和可读错误码。

三期接受 PDF、Markdown、TXT；不提供 OCR UI。上传后返回 job 并以退避轮询观察状态，避免前端假设索引立即可用。删除和重建都要体现异步状态，而不是仅切换按钮文案。

### 8.2 POST 流式问答

RAG 请求固定使用 `fetch + ReadableStream` 发送并读取 `POST /api/v1/rag/query` 的服务端流。不得使用 `EventSource`，因为该接口需要 POST 请求体和会话/查询字段。

```ts
// 源码就位后实现并测试的规划伪代码；当前未执行
const controller = new AbortController();
const response = await fetch("/api/v1/rag/query", {
  method: "POST",
  credentials: "include",
  headers: {
    "Content-Type": "application/json",
    "X-CSRF-Token": csrfToken,
  },
  body: JSON.stringify({ conversationId, query }),
  signal: controller.signal,
});

// 从 response.body 的 ReadableStream 解析 stage、heartbeat、token、citation、error、done。
```

- `stage` 显示“检索中”或“生成中”；`heartbeat` 防止长时间无反馈；`token` 追加文本；`citation` 更新来源卡片；`error` 显示可重试失败；`done` 固化结果。

- 取消按钮通过 `AbortController` 终止未完成请求。前端不得在未收到 `done` 时把回答标为完整，也不得在无引用时伪造来源卡片。

- `ChatWindow` 与 `CitationCard` 展示文档名、chunk 摘要、版本和可定位来源。会话列表来自 `/api/v1/conversations`，而不是仅保存在浏览器。

### 8.3 Caddy 链路验证

W7 的真实环境验证目标是：在实际 Caddy 同域链路下观察首个 token、持续 flush、心跳、断线、取消和重新登录后的行为。Caddy 配置只能使用其自身文档支持的设置；不得写入 Nginx 专属响应头或把其他反代示例当作 Caddy 行为。

## 9. 组件、可访问性与错误状态

| 组件族 | 规划组件 | 必须覆盖的状态 |
| --- | --- | --- |
| 通用 UI | Button、Card、Modal、Tag、EmptyState、StatusBanner | loading、empty、error、disabled、键盘焦点 |
| 站点 | Navbar、Footer、ThemeToggle、ModuleDock | 小屏导航、减少动效、公开/私有可见性 |
| 仓库 | RepoCard、RepoGrid、LanguageDot | 无数据、缓存数据、来源失败、featured 排序 |
| 背景 | BackgroundLayer、BackgroundPicker | 无权限、压缩失败、IndexedDB 不可用、恢复默认 |
| 播放器 | MusicPlayer、PlaylistPanel、ProgressBar | 媒体失败、自动播放限制、进度续播、键盘操作 |
| 鉴权 | LoginForm、SessionGate | 401、403、429、CSRF 失败、受控 redirect |
| 私有资源 | PhotoTimeline、DropZone、FileTable、MailList、MailReader | 上传进度、超限、空态、删除确认、HTML 安全降级 |
| 知识库 | DocumentManager、JobStatus、ChatWindow、MessageBubble、CitationCard | 排队、索引失败、流错误、心跳、取消、无引用 |

组件示例页必须至少覆盖关键状态，以便在不引入 Storybook 的情况下审查视觉与交互边界。Storybook 只在 M1/M2 容量与验收有余时评估。

## 10. 环境变量与构建边界

| 变量 | 用途 | 默认或规则 | 浏览器可见性 |
| --- | --- | --- | --- |
| `PUBLIC_SITE_URL` | sitemap、canonical、OG 根 URL | 仅在实际域名确认后设置 | 是 |
| `PUBLIC_API_BASE` | API 根路径 | 默认同源 `/api/v1` | 是 |
| `GITHUB_USERNAME` | 构建期仓库来源 | 未设置时跳过该来源，不虚构仓库 | 否，构建输入 |
| `GITEE_USERNAME` | 构建期仓库来源 | 未设置时跳过该来源 | 否，构建输入 |
| `GITHUB_TOKEN` | 提升 API 限流额度 | 仅 CI/服务端构建 secret，可缺省 | **否，绝不为 `PUBLIC_*`** |

前端不得声明、复制或展示 `AUTH_JWT_SIGNING_KEY`、`ADMIN_BOOTSTRAP_PASSWORD_HASH`、`MAIL_HMAC_KEY`、存储路径、模型内部端点或备份目标；这些属于总体具体开发计划中的服务端配置边界。

## 11. 前端排期与交付门禁

| 周次 | 前端任务包 | 交付物 | 验收门禁 | 低容量裁剪 |
| --- | --- | --- | --- | --- |
| W1 / 09-01 至 09-06 | `apps/web` 骨架、内容集合、`content.config.ts`、视觉令牌、公开页面、仓库 schema | 公开门户候选骨架 | 无私有 API 依赖，内容与 `featured` 规则可审查 | 延期动画和额外主题 |
| W2 / 09-07 至 09-13 | 背景、播放器进度续播、SEO、404、单一静态托管选择 | M1 候选版本 | Lighthouse 四项均不低于 90；授权媒体边界明确 | 保留进度续播，延期保活增强 |
| W3 / 09-14 至 09-20 | session store、CSRF 客户端、LoginForm、ConsoleLayout、MSW 契约 | 登录与控制台候选 | 不存 JWT，401/403/429 体验正确 | 延期控制台装饰与统计卡 |
| W4 / 09-21 至 09-27 | 相册、文件表格、DropZone、缩略图、上传状态 | 文件/照片候选 | 负例反馈和删除回滚清晰 | 延期虚拟滚动与高级布局 |
| W5 / 09-28 至 10-04 | 邮件列表、阅读、HTML 安全容器、未读状态 | 收件箱候选 | 纯文本优先、远程图片默认阻止 | 延期标签/分类/复杂筛选 |
| W6 / 10-05 至 10-11 | 同源联调、移动端回归、M2 前端验收记录 | M2 前端交接包 | Caddy/API 链路与私有路由体验通过 | 延期视觉精修 |
| W7 / 10-12 至 10-18 | kb 页面壳、DocumentManager、JobStatus、MSW 流事件 | RAG UI 骨架 | job 和流状态可解释 | 延期悬浮入口 |
| W8 / 10-19 至 10-25 | 文档上传/删除/重建、作业轮询、ReadableStream 解析 | 入库与流候选 | 事件覆盖 stage/heartbeat/error/done | 延期复杂会话 UI |
| W9 / 10-26 至 11-01 | CitationCard、会话列表、取消、Caddy 实链路验证 | 问答候选闭环 | 引用与取消正确，不伪造完成 | 延期动画与富文本美化 |
| W10 / 11-02 至 11-08 | 慢响应体验、错误降级、可访问性回归、M3 证据整理 | M3 前端交接包 | Lighthouse、引用、恢复影响的前端记录完整 | 保留基本检索体验，延期增强入口 |

## 12. 规划态测试命令与验收

以下均为**源码就位后执行的规划命令**，当前空仓库未执行，也不能据此声称通过。

```bash
# apps/web、依赖和测试夹具就位后执行的规划命令；当前未执行；Lighthouse 使用移动端默认配置
pnpm lint
pnpm test
pnpm build
pnpm exec playwright test
pnpm test:contracts
pnpm exec lighthouse http://127.0.0.1:4321
```

前端验收需要覆盖：公开页的 Lighthouse 四项均不低于 90；键盘和焦点可达；减少动效；背景失败与 IndexedDB 降级；播放器进度续播；无凭据的私有路由体验与 API 401/403；上传负例反馈；邮件 HTML/远程图片安全显示；RAG 的 `stage`、`heartbeat`、`citation`、`error`、`done`、取消与断线恢复。实际证据追加到总体具体开发计划，而不是在本文预先标记完成。

## 13. 前端风险与应对

| 风险 | 影响 | 计划应对 |
| --- | --- | --- |
| 跨页播放器保活不稳定 | 音乐中断 | 一期先验收进度续播；保活增强不阻塞 M1 |
| 背景图片过大 | LCP 或内存恶化 | 客户端压缩、目标 Blob 预算、失败时重选/降质、不保存原图 |
| 前后端进度错期 | 联调被压缩 | `packages/contracts` 与 MSW 契约先行，字段变更同步总体计划 |
| Cookie/CSRF 被误用 | 私有操作暴露 | session 状态与 token 分离、同源、CSRF + Origin，不能只靠自定义头 |
| Caddy 下流式缓冲异常 | RAG 体验退化 | W7 在实际链路验证首 token、持续 flush、心跳、断线与取消 |
| HTML 邮件或 Markdown XSS | 私有界面受损 | 服务端清洗为主、DOMPurify 为补充、远程图片默认阻止、无信任的 HTML 不执行 |
| 移动端相册/邮件可用性差 | 阶段体验不合格 | 设计初期采用折叠单栏、触摸目标和窄屏回归 |
| 范围蔓延 | M1-M3 延期 | 未达门禁不增加博客、评论、Storybook、IMAP 或 OCR |

## 14. 变更记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| v1.0 | 2026-08-27 | 前端技术方案、目录、模块与排期初稿 |
| v1.1 | 2026-08-27 | 对齐总体具体计划；修正 `featured`、同源 `/api/v1`、Cookie/CSRF、POST 流、Caddy 验证、Tailwind CSS-first 与单一静态托管策略 |
