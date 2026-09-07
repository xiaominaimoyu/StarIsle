# 星屿 StarIsle - 总体具体开发计划

> **规划警告：** 本文是跨模块的实施基线，不是实现证明。2026-08-27 的仓库仍未包含应用源码、根 `.gitignore`、提交或远程；文中路径、接口、配置、服务与命令均为后续实施约定，不能解读为已经创建、配置或运行。

> **权威顺序：** 本文为最高执行基线；若与[高层总体规划](./星屿%20StarIsle.md)或[前端专项计划](./星屿%20StarIsle%20-%20前端开发计划.md)冲突，以本文为准。高层文档保留愿景与边界，前端专项只细化 `apps/web` 的工作。

版本：v1.0  
编制日期：2026-08-27  
最近修订：2026-09-07（冻结照片墙钟时间、版本化复合游标与兼容边界；实现状态与验收证据见 `开发进度.md`）
状态：Planned / Not implemented  
读者：项目所有者、后续实施者、代码审阅者与部署维护者  
历史输入：[星屿StarIsle-个人全栈门户项目计划书.docx](./星屿StarIsle-个人全栈门户项目计划书.docx) v1.0（2026-08-27）

## 1. 执行摘要与文档控制

StarIsle 规划为一个单用户个人门户：一期交付可公开访问的静态求职展示与本地交互模块；二期交付受保护的文件、照片与邮件接收能力；三期交付仅在内部模型服务上运行的个人 RAG 知识库。十周计划按每周约 20 小时的个人开发容量编排，准备门槛另计，不占用十周。

本计划修正了四项容易导致返工的表述：GitHub 普通 REST 仓库列表并不等同于个人主页的 pinned 仓库；邮件接收的二期验收路径是 Cloudflare Email Worker 而非同时实现 IMAP；`sqlite-vec` 是 pre-v1 的起步依赖，必须有迁移阈值；仓库当前为空，任何模块、接口、测试或部署都不能被描述为已完成。

| 文档项 | 约定 |
| --- | --- |
| 文档类型 | 跨模块具体开发计划 |
| 版本规则 | 实施影响架构、契约、阶段或安全边界时，先修订本文再开始对应改动 |
| 冲突处理 | 本文优先于高层总体规划与前端专项计划 |
| 计划状态 | 所有项目均为计划；未通过验收门禁不得转为“完成” |
| 维护责任 | 项目所有者负责批准范围和门槛；实施者负责将实际结果回写为可验证证据 |

### 1.1 关键纠偏

- GitHub REST 的 `/users/{username}/repos` 用于仓库清单。所谓“置顶”必须来自本地 `featured` 清单；不得把普通 REST 列表的排序结果冒充 Profile pinned。

- 一期是“无常驻后端依赖的静态部署”，不是“没有任何运行时 JavaScript”。背景和播放器仍可使用必要的浏览器端交互代码。

- 二期邮件只验收 Cloudflare Email Worker 到 FastAPI 的安全转发链路。IMAP 是后续适配器门槛项，不与 Worker 并行实现。

- RAG 的聊天模型与 embedding 模型使用两个内部 `llama-server` 实例；FastAPI 只作为受保护的编排层，两个模型端口不得公开暴露。

### 1.2 计划成功定义

十周结束时，M1、M2、M3 都有可重复的验收记录：公开门户的移动端 Lighthouse 四项均不低于 90；私有 API 对未授权访问返回 401 或 403；上传安全负例被拒绝；测试邮件在 60 秒内可查看；至少 20 篇文档被入库；RAG 响应带引用并通过离线评测；备份可在演练环境恢复。上述是目标门槛，不是当前状态。

## 2. 当前基线、范围与容量假设

### 2.1 当前仓库基线

截至 2026-08-27，`main` 尚无 commit、无 remote、无根 `.gitignore`，也没有应用源码。当前可见内容仅为计划资料和本地工具元数据；本计划新增或修订的 Markdown 仍属于计划资料，不构成 Astro、FastAPI、Compose、Caddy、数据库或模型服务已经就位的证据。

实施开始前必须先建立可审查的最小仓库骨架、忽略规则和首个提交边界；这些动作属于后续工作，本文不执行也不假称完成。

### 2.2 十周范围

**Must（十周内必须达到）**

- 一期：静态求职门户、构建期仓库快照、本地 IndexedDB 背景、版权受控的音乐播放器、基础 SEO 与一个静态托管入口。

- 二期：单用户 Cookie 鉴权、文件与照片元数据、上传安全控制、Cloudflare Email Worker 收件链路、Caddy 同域反代、最小备份与恢复演练。

- 三期：受保护的知识库文档管理、SQLite durable job 表、内部双 `llama-server`、`sqlite-vec` 起步、带引用的流式问答、离线评测与性能分档记录。

**Should（不妨碍 Must 时纳入）**

- 亮暗主题、组件示例页、照片虚拟列表、邮件未读状态、RAG 重排、CPU/GPU profile、结构化审计事件。

**Could（只在对应门禁提前通过时纳入）**

- React Islands 的播放器保活增强、Storybook、博客内容层、富媒体相册优化、更加细分的运行报表。

### 2.3 非范围

以下内容不进入十周 MVP：博客、评论、书签、OCR、IMAP、复杂 Admin UI、多租户、Redis、复杂可观测平台、完整邮件发送、公共匿名文件分享、并行维护两套静态托管站点、将 AGPL/GPL 项目代码复制进本项目。

### 2.4 容量与裁剪规则

规划假设是每周约 20 小时个人开发时间，而非已知事实。任一周可投入时间低于 15 小时，或前一周验收未通过时，下一周必须先执行裁剪：保留 Must 目标与安全门禁，延期 Should，再延期 Could；不得以并行开启新模块弥补进度。

裁剪顺序固定为：播放器保活增强和组件示例页 → 视觉细节和语言占比条 → 邮件未读体验与照片虚拟列表 → RAG 重排与对话入口增强。鉴权、上传校验、邮件签名、引用、备份恢复和已承诺的验收指标不得裁剪。

## 3. Evidence → Finding → Path 可审计映射

本节使用一般工程审计语境：Evidence 是输入或观察，Finding 是从输入得出的约束，Path 是将约束落实到阶段、验收和变更记录的实施路径。

| ID | Evidence | Finding | 计划路径 |
| --- | --- | --- | --- |
| E-001 | 历史输入 [源 DOCX](./星屿StarIsle-个人全栈门户项目计划书.docx)，v1.0，2026-08-27 | 目标覆盖公开展示、私有数据、邮件与 RAG，且原路线采用三期十周节奏 | F-001、F-005 → P-001 |
| E-002 | 2026-08-27 当前仓库检查：`main` 无 commit、无 remote、无根 `.gitignore`、无应用源码 | 当前所有架构和命令只能作为计划；应先建立可验证的最小骨架 | F-001 → P-001 准备门槛 |
| E-003 | 已批准的 GitHub REST/README 与官方服务资料快照 | 仓库取数、模型服务、自动 HTTPS 和邮件入口有明确的接口边界，但不得把参考项目当作可直接复制的实现 | F-002、F-003、F-004 → P-001 |

| Finding | 结论 | 对计划的约束 |
| --- | --- | --- |
| F-001 | 空仓库不能证明任何功能已实现 | 每个阶段先产出最小可运行边界，再进行功能验收；本文不使用完成时态描述未来工作 |
| F-002 | REST 仓库列表不提供 Profile pinned 语义 | 使用本地 `featured` 清单、分页与 JSON 缓存；Token 仅位于 CI 或服务端 secret |
| F-003 | 邮件和私有 API 是高信任边界 | Worker 转发采用 HMAC、时间窗、nonce、Message-ID 幂等和 MIME/HTML 清洗；IMAP 不并行实施 |
| F-004 | `sqlite-vec` 适合轻量起步但处于 pre-v1 | 抽象 VectorStore，先做离线评测，再按阈值切换 Qdrant |
| F-005 | 十周乘以约 20 小时不足以安全并行七模块 | 强制分期、每周验收门禁和低于 15 小时即裁剪 |

**P-001：准备门槛 + 三期实施路径。** 2026-08-27 至 08-31 完成范围冻结、内容盘点、配置清单和仓库骨架决策；2026-09-01 至 09-13 完成 M1 静态门户；2026-09-14 至 10-11 完成 M2 私有服务；2026-10-12 至 11-08 完成 M3 RAG。每条 Finding 的实现证据应在对应周的交付物与验收记录中追加，而不是仅在本文声明。

## 4. GitHub 参考快照与借鉴边界

以下是已批准调研的 2026-08-27 快照，不在本次工作中联网刷新。stars 仅为当日观测，不是技术选型的唯一依据。每个项目都只能借鉴公开文档、结构理念或行为边界；许可证允许与否均不自动授权复制代码、资源或标识。

| 项目 | 许可证 / stars / pushed | 可借鉴 | 不能照搬 |
| --- | --- | --- | --- |
| [manuelernestog/astrofy](https://github.com/manuelernestog/astrofy) | MIT / 1433 / 2024-07-04 | Astro 个人作品集的信息架构与静态内容组织 | 不复制页面、文案、资产或项目结构的具体实现 |
| [satnaing/astro-paper](https://github.com/satnaing/astro-paper) | MIT / 4998 / 2026-08-05 | 内容站 SEO、性能意识和 Markdown 工作流 | 不把其主题、插件集合或发布机制当作强制依赖 |
| [fastapi/full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template) | MIT / 45183 / 2026-08-25 | 全栈分层、环境配置和测试组织的思路 | 不引入其完整技术栈、默认服务或身份模型 |
| [fastapi-users/fastapi-users](https://github.com/fastapi-users/fastapi-users) | MIT / 6227 / 2026-08-17 | 鉴权边界与会话撤销的参考点 | 处于 maintenance mode；仅参考，不作为默认依赖 |
| [mickael-kerjean/filestash](https://github.com/mickael-kerjean/filestash) | AGPL-3.0 / 14554 / 2026-08-26 | 文件浏览体验、存储适配边界 | AGPL 项目仅参考行为与边界，禁止复制代码 |
| [immich-app/immich](https://github.com/immich-app/immich) | AGPL-3.0 / 112746 / 2026-08-27 | 照片元数据、缩略图与隐私提示的产品边界 | AGPL 项目仅参考行为与边界，禁止复制代码 |
| [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) | MIT / 125828 / 2026-08-27 | 本地推理、OpenAI 兼容接口和资源 profile 思路 | 不承诺具体模型、性能或使用未验证的启动参数 |
| [asg017/sqlite-vec](https://github.com/asg017/sqlite-vec) | Apache-2.0 / 8047 / 2026-05-18 | 嵌入式向量检索起步路径 | pre-v1；不将其 API 稳定性视为长期保证 |
| [qdrant/qdrant](https://github.com/qdrant/qdrant) | Apache-2.0 / 34211 / 2026-08-26 | 达到规模阈值后的独立向量服务边界 | 不在未达迁移阈值时提前引入 |
| [caddyserver/caddy](https://github.com/caddyserver/caddy) | Apache-2.0 / 75236 / 2026-08-25 | 同域反代、自动 HTTPS 与简单运维模型 | 不把默认示例配置当作生产配置 |
| [webbertakken/email-worker](https://github.com/webbertakken/email-worker) | MIT / 78 / 2026-08-24 | Email Worker 的入口形态与原始 MIME 处理边界 | 不直接复用其业务逻辑或把其示例当作签名方案 |
| [navidrome/navidrome](https://github.com/navidrome/navidrome) | GPL-3.0 / 23136 / 2026-08-26 | 音乐库和播放列表的产品边界 | GPL 项目仅参考行为与边界，禁止复制代码 |

## 5. 冻结架构与规划目录树

### 5.1 架构决策

规划采用 Monorepo，但当前目录树尚未创建。公开平面负责静态求职展示，私有平面负责 Cookie 鉴权、文件、邮件和 RAG；二期起由 Caddy 在同一域名下将 `/api` 转发到 FastAPI。这样浏览器默认同源，不需要把模型服务、数据库或对象路径直接开放。

一期默认选择 Cloudflare Pages 静态托管。若账户条件不满足，唯一替换为 GitHub Pages；不得同时维护两个生产静态站点。二期同域 Caddy 接管时，静态产物与 `/api` 的最终入口以阶段门禁记录为准，避免在没有私有服务需求时过早迁移。

### 5.2 规划目录树（尚未创建）

```text
star-isle/
├── apps/
│   ├── web/                 # Astro + Tailwind；公开页面和浏览器孤岛
│   ├── api/                 # FastAPI；受保护的 /api/v1
│   └── worker/              # 第三期；同一 Python 包中的 durable job 消费者
├── packages/
│   └── contracts/           # 前后端共享的契约类型与示例，不存业务实现
├── infra/
│   ├── caddy/               # 同域反代与 TLS 计划配置
│   └── compose/             # 服务编排与 profile 计划配置
├── scripts/                 # 构建期仓库快照、维护与演练脚本
├── tests/                   # 单元、契约、端到端和恢复演练测试
└── data/                    # 运行时数据；永不提交
```

规划中的 `infra/caddy` 与 `infra/compose` 分别承载反代/TLS 和服务编排；它们与目录树中的其他路径一样尚未创建。

### 5.3 组件职责和信任边界

| 组件 | 责任 | 网络 / 信任边界 |
| --- | --- | --- |
| `apps/web` | Astro 静态页面、React Islands、构建期仓库快照显示 | 浏览器公开可读；不含任何 secret |
| `apps/api` | `/api/v1`、Cookie 鉴权、StorageAdapter、邮件验证、RAG 编排 | 仅经 Caddy 同域 `/api` 暴露；所有私有资源先鉴权 |
| `apps/worker` | 轮询 SQLite durable job 表、解析和索引文档 | 第三期引入；不对公网监听 |
| SQLite | 单用户元数据、会话、nonce、任务与审计事件 | 无 TCP 暴露；只由 API/worker 本地卷访问 |
| StorageAdapter | 原件、缩略图、附件及其路径映射 | 不以真实磁盘路径作为 URL 或 API 标识 |
| `llama-server` × 2 | chat 与 embedding 推理 | 内部网络；FastAPI 是唯一业务入口 |
| Caddy | TLS、静态产物入口、`/api` 反代 | 对外仅 80/443；不转发模型端口 |

### 5.4 关键数据流

**公开访问。** 浏览器请求静态页面和构建期仓库 JSON；公开页面不调用私有 API。Caddy/静态主机仅提供构建产物，仓库数据来自构建期分页抓取的版本化缓存。

**登录。** 浏览器先请求 `GET /api/v1/auth/csrf`，再携带 `X-CSRF-Token` 和 `Origin` 调用登录。FastAPI 验证 Argon2id 密码哈希，设置短期 access JWT Cookie 与可撤销的 refresh session；浏览器不保存 JWT 到 `localStorage`。

**仓库构建缓存。** 构建脚本对 GitHub REST 使用分页和 `per_page=100`，归一化 GitHub/Gitee 字段并写入版本化 JSON 缓存。失败时只允许使用最近成功缓存并标注构建元数据；`featured` 由本地清单决定。

**照片上传。** 已认证浏览器经 `/api/v1/photos` 上传文件。API 先执行大小、MIME 魔数、路径、符号链接、解码资源和配额校验，再由 StorageAdapter 写入不透明对象 ID；原件和缩略图分离，SQLite 只保存元数据，GPS EXIF 默认不展示。`DateTimeOriginal` 按无时区的相机当地墙钟时间处理：只接受严格的 EXIF `YYYY:MM:DD HH:MM:SS` 或 canonical `YYYY-MM-DDTHH:MM:SS`，统一投影为后者，不附加 `Z`、不猜测服务器/浏览器时区；非法或缺失值进入“未知日期”。

**邮件接收。** Cloudflare Email Worker 的 `email(message, env, ctx)` 接收原始 MIME。新协议在 `X-Mail-Key-Id` 中发送当前 `kid`，并对 `v1 + kid + timestamp + nonce + body_sha256`（点号分隔，body 使用 SHA-256）组成的规范化请求做 HMAC 后转给 FastAPI；迁移期旧协议只允许当前 key 验证三字段正文。API 随后检查五分钟时间窗、24 小时 nonce 唯一性和 Message-ID 幂等性，再解析、清洗和入库。

**RAG 问答。** 已认证浏览器以 `fetch + ReadableStream` 调用 `POST /api/v1/rag/query`。API 检索经 worker 入库的 chunk，生成带来源字段的流式事件；chat/embedding 都只在内部 `llama-server` 端口运行，提示注入内容不得取得工具、secret 或跨文档授权。

## 6. 配置与秘密清单

所有变量名以下表为准。值由部署环境提供；本文不填写用户名、域名、硬件型号或真实密钥。任何 `PUBLIC_*` 值都应当被视为客户端可见，禁止承载 secret。

| 变量 | 用途 | Secret | 作用域 | 默认策略 |
| --- | --- | --- | --- | --- |
| `GITHUB_USERNAME` | 构建期 GitHub 仓库所属用户 | 否 | 构建环境 | 未配置时不抓取，显示静态空态而不虚构数据 |
| `GITEE_USERNAME` | 构建期 Gitee 仓库所属用户 | 否 | 构建环境 | 未配置时跳过该来源 |
| `GITHUB_TOKEN` | 提升 GitHub API 限流额度 | 是 | CI 或服务端构建环境 | 可缺省；不得写入浏览器产物或仓库 |
| `PUBLIC_SITE_URL` | sitemap、canonical、OG 的公开站点根 | 否 | `apps/web` 构建环境 | 仅在已决定域名后设置 |
| `PUBLIC_API_BASE` | 浏览器 API 根路径 | 否 | `apps/web` 构建环境 | 默认同源 `/api/v1` |
| `STAR_ISLE_ENV` | API 运行模式 | 否 | API（prod overlay）/ worker（base） | prod overlay 仅向 API 设置 `production`；worker 不继承 API secret `env_file` 并按基础模式运行。API 生产必需输入缺失或非法时 fail closed，不能静默退回开发默认值 |
| `STAR_ISLE_API_ENV_FILE` | Compose 为 API 选择的环境文件路径 | 间接（文件内容可含 secret） | Compose / API | 生产候选必须显式提供；worker 不得继承该 API secret `env_file` |
| `STAR_ISLE_SITE_ADDRESS` | Caddy 的站点地址 | 否 | prod Compose / Caddy | 真实域名确定后显式提供；本候选未验证真实域名 |
| `ACME_EMAIL` | Caddy 的 ACME 联系邮箱 | 否（个人信息） | prod Compose / Caddy | 生产输入，避免写入日志；本候选未验证 ACME |
| `CADDY_HOST_PORT` | local 候选的 Caddy 宿主发布端口 | 否 | local Compose | 必须绑定 loopback，不得由默认值扩大为公网监听 |
| `CADDY_HTTP_HOST_PORT` | prod Caddy HTTP 宿主发布端口 | 否 | prod Compose | 生产候选显式发布 `80` |
| `CADDY_HTTPS_HOST_PORT` | prod Caddy HTTPS 宿主发布端口 | 否 | prod Compose | 生产候选显式发布 `443` |
| `AUTH_JWT_SIGNING_KEY` | access JWT 签名 | 是 | API | 必须由 secret 注入，支持 `kid` 轮换 |
| `AUTH_JWT_ACTIVE_KID` | 当前 JWT 密钥版本 | 否 | API | 轮换时显式变更，不猜测默认值 |
| `AUTH_JWT_PREVIOUS_KEYS_JSON` | 仅用于验证尚未过期旧 access JWT 的 `{kid: secret}` JSON 映射 | 是 | API | 可选、最多保留 4 把旧密钥；当前 `kid` 禁止重复；超过最长 access TTL 后移除旧项 |
| `ADMIN_BOOTSTRAP_PASSWORD_HASH` | 单用户初始密码的 Argon2id 哈希 | 是 | API 初始化 | 只接受已哈希值，不接受明文环境变量 |
| `AUTH_ACCESS_TTL_MINUTES` | access JWT 有效期 | 否 | API | 初始基线 15 分钟 |
| `AUTH_REFRESH_TTL_DAYS` | refresh session 有效期 | 否 | API | 初始基线 7 天，可撤销 |
| `COOKIE_SECURE` | Cookie 的 Secure 属性 | 否 | API | 生产环境必须为真；开发环境另有明确策略 |
| `DATA_ROOT` | 运行时数据根目录 | 否 | API / worker | 位于 `data/` 对应卷，永不提交 |
| `DATA_HOST_ROOT` | Compose 宿主侧数据卷根 | 否 | Compose host input | 所有 base + local / prod 组合（含或不含 CPU overlay）均必须显式提供；缺失即 preflight 拒绝 |
| `MODEL_HOST_ROOT` | Compose 宿主侧模型卷根 | 否 | Compose host input | 仅 local-cpu / prod-cpu（叠加 CPU overlay）必须显式提供；缺失即 preflight 拒绝 |
| `FILE_MAX_BYTES` | 一般文件上限 | 否 | API | 初始建议 100 MB，按部署容量调整 |
| `IMAGE_MAX_BYTES` | 图片上限 | 否 | API | 初始建议 20 MB，按部署容量调整 |
| `UPLOAD_MAX_CONCURRENCY` | 前端建议并发上传数 | 否 | web | 初始建议 3，服务端仍独立限流 |
| `MAIL_HMAC_KEY` | Email Worker 到 API 的当前 HMAC secret | 是 | Worker / API secret | 必填；只与当前 `kid` 配合签发/验证，不遍历猜测旧密钥 |
| `MAIL_HMAC_ACTIVE_KID` | 当前邮件 HMAC 密钥版本 | 否 | Worker / API | 新协议必填安全标识；先升级 API、再升级 Worker，最后才能旋转 secret |
| `MAIL_HMAC_PREVIOUS_KEYS_JSON` | API 仅用于验证新协议在途重试的 `{kid: secret}` JSON 映射 | 是 | API | 可选、最多 4 把旧密钥；禁止当前 `kid`、重复 secret 与不安全旧 key；排空重试窗口后移除 |
| `MAIL_MAX_BYTES` | 接收原始 MIME 大小上限 | 否 | Worker / API | 在部署前按容量明确，不接受无限制邮件 |
| `MAIL_RETENTION_DAYS` | 邮件与附件保留策略 | 否 | API / worker | 由 Compose 统一向 API / worker 提供相同值，再按个人隐私与容量决策 |
| `LLAMA_CHAT_BASE_URL` | chat `llama-server` 内部端点 | 否 | API | 仅容器内部 URL，禁止公网地址 |
| `LLAMA_EMBED_BASE_URL` | embedding `llama-server` 内部端点 | 否 | API / worker | 仅容器内部 URL，禁止公网地址 |
| `CHAT_MODEL_ID` | 对话模型标识 | 否 | API | 通过离线评测和资源 profile 选择 |
| `EMBEDDING_MODEL_ID` | embedding 模型标识 | 否 | API / worker | 与已建索引绑定版本记录 |
| `EMBEDDING_DIM` | 向量维度 | 否 | API / worker | Compose 必须向 API / worker 提供相同值，且与模型和 VectorStore 记录一致 |
| `BACKUP_TARGET` | 加密备份目标位置 | 是 | 运维任务 | 必须在恢复演练前可用，禁止写入仓库 |

## 7. API 契约初稿

本节是契约草案，不声称 OpenAPI 已生成。所有业务 API 以 `/api/v1` 为根；成功体采用资源对象或 `{ "data": ... }`，失败体采用统一结构。前端必须按同源 Cookie 工作，不能假设 token 位于浏览器存储。

```json
{
  "error": {
    "code": "AUTH_REQUIRED",
    "message": "需要有效会话",
    "requestId": "可追踪请求标识"
  }
}
```

### 7.1 通用约定

| 主题 | 约定 |
| --- | --- |
| 鉴权 | 私有端点要求 Secure、HttpOnly、SameSite Cookie；无会话返回 401，已认证但无权返回 403 |
| CSRF | `GET /auth/csrf` 返回会话绑定 token；所有 unsafe 请求带 `X-CSRF-Token` 并校验 `Origin` |
| 分页 | 列表使用 `cursor`、`limit` 与 `nextCursor`；默认和最大 limit 在实现时公开为契约字段 |
| 幂等 | 文档入库、文件上传等可重试写操作接受 `Idempotency-Key`；邮件由 Message-ID + nonce 额外保护 |
| 错误 | 400 参数错误、401 未认证、403 禁止、404 不存在、409 冲突、413 超限、415 类型不支持、422 语义校验、429 限速、5xx 服务异常 |
| 流式响应 | `POST` 流固定使用 `fetch + ReadableStream`；不用 `EventSource`，因为 `EventSource` 不能表达本契约的 POST 请求体 |

### 7.2 鉴权端点

| 端点 | 请求 / 响应重点 | 鉴权与状态 |
| --- | --- | --- |
| `GET /auth/csrf` | 响应 `{ csrfToken, expiresAt }` | 创建或关联匿名握手；token 与随后会话绑定 |
| `POST /auth/login` | 请求 `{ password }`；响应当前用户摘要并设置 Cookie | 要求 CSRF + Origin；错误 401，限速时 429 |
| `POST /auth/refresh` | 无业务体；轮换 access JWT | 要求有效 refresh session + CSRF；失效 401 |
| `POST /auth/logout` | 无业务体；撤销当前 refresh session 并清 Cookie | 已登录 + CSRF；成功 204 |
| `GET /auth/me` | 响应 `{ id, role, sessionExpiresAt }` | 已登录；成功 200，未登录 401 |

### 7.3 文件、照片与邮件端点

| 端点 | 请求 / 响应重点 | 鉴权、幂等与状态 |
| --- | --- | --- |
| `GET /files` | `cursor`、`limit`、可选类型过滤；响应 `{ items, nextCursor }` | 已登录；200 |
| `POST /files` | `multipart/form-data` 的文件与可选标签 | 已登录 + CSRF + `Idempotency-Key`；201、413、415、422 |
| `GET /files/{fileId}/download` | 短生命周期下载响应，不暴露真实路径 | 已登录；200、404 |
| `DELETE /files/{fileId}` | 无业务体 | 已登录 + CSRF；204、404；按保留策略软删后清理 |
| `GET /photos` | 版本化复合 keyset 游标与缩略图元数据；`captureAt` 为 canonical 秒级墙钟或 `null` | 已登录；200 |
| `POST /photos` | 图片上传；响应 `{ id, status, thumbnailState }` | 已登录 + CSRF + 幂等；201 或 202 |
| `DELETE /photos/{photoId}` | 无业务体 | 已登录 + CSRF；204 |
| `GET /mail` | 列表游标、未读过滤 | 已登录；200 |
| `GET /mail/{messageId}` | 纯文本、已清洗 HTML 摘要、附件元数据 | 已登录；200、404 |
| `POST /mail/{messageId}/read` | 请求 `{ read: true }` | 已登录 + CSRF；200、404 |

### 7.4 知识库、会话、作业与健康端点

| 端点 | 请求 / 响应重点 | 鉴权、幂等与状态 |
| --- | --- | --- |
| `GET /knowledge/documents` | 文档、版本、索引状态、游标 | 已登录；200 |
| `POST /knowledge/documents` | `multipart/form-data`，仅 PDF / Markdown / TXT | 已登录 + CSRF + 幂等；202 返回 `{ documentId, jobId }` |
| `DELETE /knowledge/documents/{documentId}` | 标记删除与向量清理任务 | 已登录 + CSRF；202 或 204 |
| `POST /knowledge/documents/{documentId}/rebuild` | 请求 `{ reason }` | 已登录 + CSRF；202 返回 job |
| `POST /rag/query` | `{ conversationId?, query }`；`fetch` 读取事件流 | 已登录 + CSRF；200 流、409 索引未就绪、429、5xx |
| `GET /conversations` | 会话列表、游标 | 已登录；200 |
| `POST /conversations` | `{ title? }` | 已登录 + CSRF；201 |
| `GET /conversations/{conversationId}` | 消息与引用摘要 | 已登录；200、404 |
| `GET /jobs/{jobId}` | `{ status, progress, errorCode?, updatedAt }` | 已登录；200、404 |
| `GET /health/live` | 进程存活摘要 | 仅部署探针；200 或 503 |
| `GET /health/ready` | 数据库、存储、必要内部依赖就绪摘要 | 仅部署探针；200 或 503 |

RAG 流初始事件为 `stage`、`heartbeat`、`token`、`citation`、`error`、`done`。每个 `citation` 至少包含 `documentId`、文档显示名、chunk 标识、片段和版本；前端通过 `AbortController` 取消未完成的请求。事件格式和字段在首个契约测试前冻结，避免前后端各自猜测。

## 8. 鉴权、文件、邮件与 RAG 的专项基线

### 8.1 单用户鉴权与浏览器边界

- access JWT 初始有效期为 15 分钟，仅存放于 Secure、HttpOnly、SameSite Cookie；refresh session 初始有效期为 7 天，可撤销、可轮换并关联设备/会话审计信息。

- 密码只保存 Argon2id 哈希。登录端点必须限速，失败响应不泄露账号是否存在；JWT 密钥采用 `kid` 管理：`AUTH_JWT_SIGNING_KEY` 只配合 `AUTH_JWT_ACTIVE_KID` 签发新 token，可选的 `AUTH_JWT_PREVIOUS_KEYS_JSON` 只验证仍在最长 access TTL 内的旧签名。未知 `kid`、header/payload `kid` 不一致、当前 `kid` 在旧映射中重复或旧密钥配置不安全时必须拒绝，不能回退到当前密钥猜测验证。

- `GET /auth/csrf` 返回与会话绑定的 token。登录、刷新、退出、上传、删除、标记已读、入库和重建等 unsafe 请求必须发送 `X-CSRF-Token` 并校验 `Origin`；不得以 `X-Requested-With` 作为唯一防护。

- CORS 默认不开放跨域。当前设计以同源为前提；若将来改变域名拓扑，必须先单独设计 allowlist、Cookie 站点属性和预检规则。

### 8.2 文件与照片的 StorageAdapter 边界

- 用 `StorageAdapter` 抽象本地卷、对象存储或未来迁移目的地；API 标识使用随机、不可预测的对象 ID，禁止将用户文件名和物理路径作为访问授权凭据。

- SQLite 保存 asset/photo 元数据，原件与缩略图分开保存。文件、图片、邮件附件都要有来源、大小、MIME、哈希、创建时间、状态和保留策略字段。

- 初始可配置建议为图片 20 MB、一般文件 100 MB、浏览器并发上传 3。上限不是硬件事实，部署前需按可用存储和带宽调整。

- 服务端验证 MIME 魔数、扩展名与宣称类型的一致性；拒绝路径穿越、符号链接越界、超大像素图、异常解码开销和超配额写入。EXIF 中的 GPS 默认不向 UI 展示；原 EXIF 是否保留由隐私策略明确记录。

- 相册列表把有效 `capture_at` 与未知时间分成两个明确排序域：有效拍摄时间按 canonical 本地墙钟值倒序并先展示；无效或缺失值归入“未知日期”，内部按 `created_at` 倒序。旧 EXIF 文本只在查询投影中规范化，不做猜测性数据迁移；改变排序语义时必须版本化照片 cursor scope，使旧游标固定失败并从第一页重取。

### 8.3 邮件接收基线

二期唯一验收路径是 Cloudflare Email Worker。Worker 的 `email(message, env, ctx)` 获取原始 MIME 后，不向浏览器转发原始内容。新协议发送 `X-Mail-Key-Id`，并以 `v1 + kid + timestamp + nonce + body_sha256`（点号分隔）的规范化数据计算 HMAC；`kid` 因而同时绑定密钥选择与签名正文。API 只按精确 `kid` 选择当前或旧映射中的一把 key，未知/非法 `kid`、当前 `kid` 冲突、重复 secret 或不安全旧 key 均拒绝，绝不遍历密钥猜测验证。

为允许无停机升级，API 可在迁移阶段接受“不含 `X-Mail-Key-Id`、仍签 `timestamp + nonce + body_sha256`”的旧请求，但该固定 legacy 路径只能使用当前 `MAIL_HMAC_KEY`，不得命中 previous map。部署顺序固定为：先上线双协议 API；再让 Worker 配置当前 `MAIL_HMAC_ACTIVE_KID` 并切到新协议；确认旧 Worker 与在途 legacy 重试均排空后，才旋转 active `kid`/secret 并把上一把 key 暂存 previous map。真实 Cloudflare secret 绑定、排空窗口、回滚与移除时间仍须由部署所有者验收。

API 验证 HMAC、五分钟时间窗、nonce 在 24 小时内唯一，以及 Message-ID 的幂等约束。之后才进行 MIME 解析、大小限制、附件分类、纯文本提取和 HTML 清洗。远程图片默认不加载，HTML 邮件不得以受信任页面脚本执行；邮件发送功能不在范围内。

IMAP 只在“Email Worker 无法满足域名/账户条件、且已经评估轮询延迟与凭据保管”这一决策门之后作为适配器候选，不得在二期与 Worker 并行开发。

### 8.4 RAG 基线与提示注入边界

RAG 只接受 PDF、Markdown、TXT；三期不做 OCR。`apps/worker` 从 SQLite durable job 表轮询可重试任务，不引入 Redis。初始离线基线为：chunk 600 tokens、overlap 100、召回 top 20、重排 top 6、30 题离线评测集。参数可由离线评测调整，但所有调整都必须记录模型版本、embedding 维度、语料版本和指标变化。

`VectorStore` 是唯一的向量访问抽象。起步使用 `sqlite-vec`，并明确其 pre-v1 风险；Qdrant 只在第 18 节阈值触发后评估。chat 和 embedding 使用独立的内部 `llama-server` 实例及端口，FastAPI 不能把它们代理为公网原始模型接口。

入库文档是不可信内容：文档中的“忽略规则”“泄露密钥”“调用工具”等文本只可作为检索语料，不能覆盖系统约束、权限、数据隔离或返回策略。回答需携带引用，且不得把来源片段以外的私有内容当作已证实事实。

## 9. 数据模型、存储布局与恢复策略

### 9.1 逻辑数据模型

| 实体 | 核心字段与索引 | 状态、删除与保留 |
| --- | --- | --- |
| `admin` | `id`、密码哈希、角色、created/updated；单用户唯一约束 | 禁止常规删除；bootstrap 后由受控维护流程修改 |
| `session` | `id`、admin_id、refresh 哈希、expires_at、revoked_at；按 admin 和过期时间索引 | 过期或撤销后不可刷新；按保留周期清理 |
| `csrf` | session_id、token 哈希、expires_at；会话关联索引 | 轮换或会话撤销即失效 |
| `asset` | 不透明 ID、原始名、MIME、大小、对象键、哈希、状态、唯一 `storage_class`、GC 时间/尝试/错误字段 | schema v5 按类别软删后进入清理期；成功物理删除保留 `deleted` tombstone 并写 `object_purged_at` |
| `photo` | asset_id、capture_at、thumbnail_asset_id、EXIF 摘要；物理表保留 capture_at 索引，列表以严格规范化投影排序 | capture_at 是无时区墙钟值；非法/缺失值归入未知日期；GPS 默认不投影到 UI；与 asset 一致的删除策略 |
| `mail_message` | Message-ID、发件/收件摘要、主题、received_at、body 状态；Message-ID 唯一索引 | 按 `MAIL_RETENTION_DAYS` 清理；HTML 与原始 MIME 分层保存 |
| `mail_attachment` | message_id、asset_id、MIME、大小 | 跟随邮件保留策略；不因展示而自动公开下载 |
| `mail_nonce` | nonce、received_at、expires_at；nonce 唯一索引 | 24 小时后清理，防止重放 |
| `knowledge_document` | asset_id、标题、类型、版本、hash、index_state | 删除触发向量清理任务；保留失败原因 |
| `chunk` | document_id、序号、文本、token_count、hash；文档+序号唯一索引 | 随文档版本替换；可追踪引用 |
| `vector_ref` | chunk_id、store、向量标识、embedding 模型/维度 | 索引重建时双写/切换需有事务式状态记录 |
| `ingestion_job` | 类型、payload、状态、尝试数、next_run_at、错误码 | SQLite durable 队列；失败可重试，超过上限进入人工审阅态 |
| `conversation` / `message` | 会话、消息角色、内容、引用摘要、created_at | 支持按保留策略软删；不把模型上下文无限增长 |
| `audit_event` | actor、动作、资源类型/ID、请求标识、结果、时间 | 结构化、脱敏；按最小保留期与容量复核 |

### 9.2 规划存储布局

```text
data/                              # 运行时数据；永不提交
├── db/                            # SQLite 文件与迁移后的备份副本
├── objects/
│   ├── originals/                 # file、photo-original、knowledge 的不透明对象
│   ├── thumbnails/                # photo-thumbnail 的照片缩略图
│   ├── mail-attachments/          # 邮件附件对象
│   └── mail-raw/                  # 私有原始 MIME；不公开、不可下载/渲染，跟随邮件保留期清理
├── cache/
│   └── repos/                     # 构建期仓库 JSON 版本化缓存
├── models/                        # 已批准的本地模型文件，独立于源码管理
└── backups/                       # 本地暂存；正式备份目标由 BACKUP_TARGET 决定
```

路径只是规划，不表示目录已经存在。schema v5 的 `storage_class` 是 asset 唯一目录归属：`file`、`photo-original`、`knowledge` 映射到 `originals`，`photo-thumbnail` 映射到 `thumbnails`，`mail-attachment` 映射到 `mail-attachments`；`unknown` 没有映射，只作为历史隔离态。Adapter 必须在每次读写前验证解析后的目标仍在 `DATA_ROOT` 内，拒绝符号链接越界；数据库迁移以版本号、向前迁移、失败中止和可验证备份为原则，不执行无备份的破坏性结构改动。运行时 GC 不扫描孤儿对象，也不猜测 unknown 的归属。

### 9.3 备份与恢复

最小备份集包括 SQLite 一致性快照、由 destination snapshot 逻辑引用的对象数据、配置清单的 secret 引用说明、模型/索引版本记录和恢复日志；不应递归复制 orphan、`objects/health` 或 SQLite sidecar。实现必须先由 source 连接 A 获取有界 `BEGIN IMMEDIATE` 协调锁，再由独立只读连接 B 执行 online backup；A 必须持续到目标 SQLite、逻辑对象和原子 manifest 都完成。output 根必须以独占 incomplete marker 表示构建中，marker 存在时 verify/restore 一律拒绝，成功时最后移除；失败 partial 只能由运维显式检查/处理。恢复演练必须在隔离位置验证：数据库可打开、对象计数符合记录、至少一个照片缩略图可关联、至少一个知识库文档可检索、密钥未被写入日志。schema >=5 的恢复还必须验证 asset 类别的精确路径、大小/SHA、unknown 在三个非 raw 目录中的唯一安全候选，以及 purged tombstone 不再有映射对象。备份成功不等于恢复成功，因此每个里程碑都把恢复验证纳入门禁；非协作的手工文件修改不受 SQLite 门禁约束，且长时间备份可能使正常写者超时，不能把格式校验或协调锁当作恢复演练完成。

当前源码提供的 `recovery_drill.py` 只是一道受控的 isolated local candidate 门禁：它要求候选是已完成的 format v3/schema v5 备份，先严格校验候选实际文件集合与 DB/reparse 安全，再在新建 target 中验证只读 SQLite、manifest/实际对象闭包、逻辑对象引用、照片关联以及 ready 文档的实际 `rag_engine.search`。backup、target、report 都必须是绝对安全路径并与运行时默认 `data/` 和显式 `DATA_ROOT` 隔离；backup 既不能位于这些根内、也不能包含这些根，三者不得彼此重叠。format v3 的 manifest 和实际对象目录只允许 `originals`、`thumbnails`、`mail-attachments`、`mail-raw`，历史 format v2 才可兼容 `health`。query 仅从受限 stdin 输入。启动时它原子创建 report 的同目录 incomplete marker 并发布 `running`，成功后才为 `passed` 并删除 marker；任何失败都由运维显式检查和清理 target/report partial。report 固定为 `scope=isolated-local-candidate`、`productionAccepted=false`，不能替代真实授权语料、真实模型/profile、部署环境或生产恢复验收，也不改变 M1/M2/M3 的完成判定。

## 10. 七模块实施手册

### 10.1 个人展示与求职页

- **入口条件：** 准备门槛已冻结内容源、视觉令牌和公开信息边界；没有把私人数据写入公开 Markdown。

- **任务分解：** 在 `apps/web` 规划 Astro Content Collections、首页、项目页、简历页、404、站点元信息、sitemap、robots 和打印样式；交互仅以必要 Islands 挂载。

- **规划验证：** 在源码就位后检查公开页不请求私有 API、关键图片有尺寸信息、移动端 Lighthouse 四项达到门槛。

- **验收：** M1 可访问、内容可更新、公共页面不暴露 secret 或私有导航数据。

- **裁剪：** 先保留内容、SEO、可访问性和静态部署；星空视觉、复杂动画和扩展主题属于 Should/Could。

### 10.2 GitHub / Gitee 仓库展示

- **入口条件：** 已知所属用户名配置项与本地 `featured` 清单格式；无需把用户名预填进本文。

- **任务分解：** 构建期分页抓取，GitHub 使用 `per_page=100`；归一化 `source/name/description/url/language/stars/forks/updatedAt/featured/topics`；写入版本化 JSON 缓存并保存采集时间和来源。

- **规划验证：** 模拟分页、限流和来源失败；确认失败时显示最后成功缓存的元数据而不是伪造实时数据。

- **验收：** 仓库卡片数据可追溯到构建缓存，`featured` 只来自本地清单；Token 未进入公开产物。

- **裁剪：** 语言占比和附加 API 查询优先延期，基础仓库卡片与缓存不可裁剪。

### 10.3 自定义背景

- **入口条件：** 浏览器支持 IndexedDB，且隐私文案说明背景只保存在同一浏览器与设备，不承诺跨设备同步。

- **任务分解：** 选图、客户端压缩、Blob 存储、预设背景、透明度/模糊度、恢复默认和可读性遮罩；压缩失败时提示重选或降低质量，不回退保存超大原图。

- **规划验证：** 在大图、损坏图、超限图、IndexedDB 不可用和减少动效偏好下检查降级与文字对比。

- **验收：** 刷新后同浏览器背景仍存在，首屏不被背景解码阻塞，图片不上传到服务端。

- **裁剪：** 只保留预设背景与恢复默认；高级滤镜和多背景图库延期。

### 10.4 音乐播放器

- **入口条件：** 仅有自有、授权或公版样例；播放列表与版权说明准备完成。

- **任务分解：** 使用 HTML5 Audio、播放列表、播放/暂停、进度续播、音量、键盘可达性和全局状态；`ClientRouter` 或 `transition:persist` 保活仅作为增强。

- **规划验证：** 测试媒体加载失败、移动端自动播放限制、切页、输入框焦点和无障碍操作。

- **验收：** 一期核心是进度续播；保活增强失败不得阻塞 M1。

- **裁剪：** 歌词、可视化和复杂队列延期；不得为了展示引入无授权音乐。

### 10.5 文件与照片存储

- **入口条件：** Cookie 鉴权、StorageAdapter、SQLite 元数据迁移、上传限制和备份路径已在二期环境确认。

- **任务分解：** 文件列表、上传、下载、删除；照片缩略图、时间轴、EXIF 摘要、配额、对象清理与审计事件；原件和缩略图分别存储。通用 GC 只处理 `file`、`photo-original`、`photo-thumbnail`、`knowledge`，在 SQLite 写锁中重读 live reference；共享资产保持 active，邮件附件/原始 MIME 生命周期不由该 GC 接管。

- **规划验证：** 未授权、路径穿越、符号链接、伪造 MIME、超大小、超像素、配额耗尽、重复重试和删除恢复窗口等负例。

- **验收：** 私有资源不被匿名读取；上传安全负例被拒绝；照片不默认展示 GPS；恢复演练可还原元数据和对象关联。

- **裁剪：** 保留原件、缩略图和列表；虚拟滚动、复杂编辑和对象存储迁移延期。

- **v5 生命周期约束：** 通用 GC 在 SQLite 写锁中重读 tombstone、类别、到期时间与 live reference；陈旧候选不得穿透已写入的退避。照片删除仅可排队角色精确匹配的 `photo-original` / `photo-thumbnail` 资产；跨类或同 ID 双角色关系只删除元数据。`unknown`、邮件附件和原始 MIME 均不进入通用 GC。

### 10.6 邮件接收

- **入口条件：** 邮件域名和 Cloudflare 条件已确认，Worker/API 有共享的 HMAC secret 引用，且原始 MIME 上限、附件策略和保留期已确定。

- **任务分解：** Worker 接收入口、规范化 HMAC、API 验证、nonce、Message-ID 幂等、MIME 解析、HTML 清洗、远程图片默认阻止、收件箱列表与详情。

- **规划验证：** 测试签名错误、过期 timestamp、重放 nonce、重复 Message-ID、超大 MIME、危险 HTML、远程像素和附件异常。

- **验收：** 合法测试邮件在 60 秒内可查看；上述攻击性或异常输入被拒绝或安全降级；不提供发送能力。

- **裁剪：** 收件箱高级分类和未读体验可延期；HMAC、幂等、清洗和大小限制不可裁剪。

### 10.7 RAG 知识库

- **入口条件：** 内部 chat/embedding 模型实例可达、资源 profile 记录、PDF/Markdown/TXT 样本经过授权确认、评测集和引用字段定义完成。

- **任务分解：** 文档上传、durable job、解析切块、embedding、VectorStore、索引状态、重建、会话、流式查询、引用卡片、离线评测和备份记录。

- **规划验证：** 测试损坏文档、重复文档、取消作业、模型不可达、索引重建、提示注入文本、无引用回答和 CPU/GPU 超时体验。

- **验收：** 至少 20 篇文档完成入库；30 题离线评测有记录；每个展示回答带来源；CPU 目标为单次响应小于 60 秒，GPU 目标为小于 10 秒量级。

- **裁剪：** 优先保留可引用的基本检索；重排、悬浮入口和复杂会话管理可延期。OCR、Redis 和 Qdrant 不随三期默认引入。

## 11. Compose、Caddy 与部署拓扑计划

### 11.1 服务与端口暴露

| 服务 | 计划责任 | 对外端口 | 内部端口 / 访问规则 | 健康与数据 |
| --- | --- | --- | --- | --- |
| Caddy | TLS、静态入口、`/api` 同域反代 | 80、443 | 反代到 API；不暴露模型端口 | 记录证书与反代健康，不记录 secret |
| web 静态产物 | 一期公开门户，二期可由 Caddy 提供构建产物 | 经 Caddy 或单一静态主机 | 不提供私有数据 | 构建元数据与静态健康页 |
| FastAPI | `/api/v1`、鉴权、文件、邮件、RAG 编排 | 无直接公开端口 | 计划内部 8000；仅 Caddy 可达 | `/health/live`、`/health/ready` |
| worker | durable job 消费与索引 | 无 | 无监听端口；访问 SQLite/模型内部网络 | job 延迟、失败数、最近成功时间 |
| llama-chat | 对话推理 | 无 | 计划内部 8080；仅 API 可达 | 模型加载与就绪探针 |
| llama-embed | embedding 推理 | 无 | 计划内部 8081；仅 API/worker 可达 | 模型加载与就绪探针 |
| SQLite / Storage | 元数据与对象 | 无 | 本地卷，不暴露 TCP | 一致性快照、容量与恢复演练 |

### 11.2 Profile、升级与回滚

Compose 规划两个互斥 profile：`cpu` 是基础可用路径，`gpu` 只在已确认驱动、设备映射和资源余量的主机启用。二者不能被理解为同时必需；模型选择和 token 限制要随 profile 记录。

升级前必须创建数据库一致性备份、记录镜像/模型版本与配置变更摘要；升级后执行健康检查和最小回归；失败时回到前一已知可用镜像/配置组合并验证数据可读。回滚不是删除数据，也不能覆盖更新后的数据库而不先保存故障副本。

最小监控集为结构化日志、健康检查、备份状态和恢复演练结果。当前本地候选以标准库单行 JSONL 输出固定的 UTC 时间、level、component、event、result，并只按白名单附加安全 request ID、路由模板/状态、ready 布尔值、稳定错误码、审计旁路状态或聚合计数；项目自带 Uvicorn 不保留 raw access log。不得记录原始 URL/query/header/body、密码、Cookie、JWT、HMAC、邮件正文全量、私人文档全量、对象键、路径、哈希、模型标识或 secret。`/health/ready` 的公共响应契约不因日志新增字段。不引入复杂可观测平台，除非第 18 节门槛明确触发。

### 11.3 当前部署拓扑安全候选（2026-09-01，Sol 终审通过；未运行）

当前候选以 `compose.yml` 为 base，按目标叠加 `compose.local.yml` 或 `compose.prod.yml`，并按需叠加 `compose.cpu.yml`。local 仅把 Caddy 发布到 loopback；prod 通过 Caddy 环境输入显式发布 `80/443`。`DATA_HOST_ROOT` 是所有 base + local / prod 组合必需的 host input；`MODEL_HOST_ROOT` 仅 local-cpu / prod-cpu 的 CPU overlay 组合必需。Caddyfile 或 `apps/web/dist` 宿主源缺失时，long bind 禁止自动创建空目录并失败。

API 可使用 `STAR_ISLE_API_ENV_FILE` 引入自己的 secret `env_file`，worker 不继承该文件；Compose 仍必须使二者获得相同的 `MAIL_RETENTION_DAYS` 和 `EMBEDDING_DIM`。`STAR_ISLE_ENV=production` 下缺少或非法生产输入必须 fail closed。gzip 只在静态 handle；API/SSE 不经过该 encode，也不设置负值 flush override，配置意图保留 Caddy 对 `event-stream` 的自动立即刷新和默认断连取消，但本轮未运行验证。

该候选由合成 `deployment_preflight` 与 Compose config 进行检查，不能由此推导 Caddyfile 语法或 Caddy runtime 已验证；本机 Caddy CLI 未安装且未运行。未执行 Docker build/up/run/pull/create/down、Caddy runtime、真实域名/email/`PUBLIC_SITE_URL`/API 环境输入、TLS/ACME/CDN、SSE 首字节/heartbeat/cancel、worker runtime health/job smoke 或部署，因此不标记 M1/M2/M3 完成。

## 12. 安全、隐私与许可证清单

| 主题 | 风险 | 计划控制 | 验证门禁 |
| --- | --- | --- | --- |
| 鉴权 | JWT 被脚本读取、会话不可撤销 | HttpOnly Cookie、短 access、可撤销 refresh、Argon2id、限速、密钥轮换 | 浏览器存储检查、过期/撤销/限速契约测试 |
| CSRF / CORS | Cookie 被跨站滥用 | 会话绑定 CSRF、Origin 校验、同源默认、无宽泛 CORS | unsafe 请求无 token 或 Origin 不符时拒绝 |
| 上传 | 路径穿越、伪装 MIME、解码炸弹、超配额 | 魔数、尺寸、像素、路径/符号链接、配额、随机对象 ID | 负例上传必须失败且不遗留对象 |
| 邮件 | 重放、恶意 HTML、追踪像素、附件滥用 | HMAC、窗口、nonce、幂等、清洗、远程图片默认阻止 | 签名/时窗/HTML/重复消息测试 |
| RAG | 文档提示注入、无依据回答、模型端口泄露 | 内部模型端口、来源引用、上下文隔离、输入不提升权限 | 注入样本与无引用样本测试 |
| EXIF / 隐私 | GPS、设备信息和日志泄露 | GPS 默认隐藏、最小元数据、日志脱敏与留存规则 | UI、下载和日志审查 |
| Secret | 密钥进入仓库、构建产物或日志 | secret 仅环境注入，`PUBLIC_*` 禁止 secret，轮换记录 | 提交前扫描与构建产物检查 |
| 依赖许可 | 不当复制或义务遗漏 | 许可证登记、AGPL/GPL 仅参考行为/边界 | 依赖审阅与来源记录 |

## 13. 测试策略与未来验证命令

以下命令均为**源码就位后执行的规划命令**，当前空仓库未运行，也不能从本文推导运行结果。实际命令名、工作目录和测试夹具需在首个实现提交时与目录结构一起确认。

### 13.1 测试层次

- **静态与单元：** TypeScript、Astro、格式检查、前端状态逻辑、Python 业务规则、上传解析、HMAC 验证、会话撤销和 job 状态迁移。

- **契约：** `packages/contracts` 中的请求/响应示例、错误体、分页、CSRF、流事件和前后端兼容性。

- **端到端：** 公开门户、登录、未授权拒绝、上传负例、邮件链路、RAG 引用/取消、移动端布局。

- **部署与恢复：** Compose 语法、健康端点、Caddy 链路、首 token/持续 flush、备份恢复与数据一致性。

### 13.1.1 本机测试 harness 的已实现边界（2026-08-31，历史中间记录；已由 13.1.3 Sol 终审通过结论覆盖）

pytest collection 在导入业务应用前固定 test-only 环境，并只在未提供根目录时创建/清理自有系统临时 `DATA_ROOT`；外部 root 必须是经验证系统临时目录的严格子目录、不得经 symlink/reparse point 逃逸，且绝不由 harness 删除。独立 API/browser/mail smoke 将样本、截图、私有邮件回执和日志限定在各自拥有的临时目录；browser 只有通过新的、尚不存在且不在仓库或 `DATA_ROOT` / `DATA_HOST_ROOT` 内的系统临时 `--evidence-dir`（或兼容的显式 `STAR_ISLE_E2E_EVIDENCE`）才会保留证据，child 以排他方式创建该目录，既有目录不会复用或覆盖，截图可能含私有 UI。完整 browser E2E 仅接受无 userinfo/path/query/fragment 的 loopback API/Web origin；`--public-only` 才可检查明确指定的远程公开静态站点。

普通 harness 输出只包含固定检查名、状态、安全计数、HTTP 方法和固定路由模板/status；未知路径为 `/other`，不保留动态 ID、对象键或文件名，不应写出正文、回答、邮件主题/ID、CSRF、完整 URL、路径、cookie 或异常原文。该工作不改变 RAG keyword/vector retrieval、评测指标、authorized gate 或 acceptance 策略；真实部署、真实私有数据、Cloudflare 与 Lighthouse 仍须独立验收。既有 `scripts/.e2e-browser-evidence` 与历史系统临时目录不属于 harness 所有权，未读取或清理。

### 13.1.2 harness 补强事实（2026-09-01，历史中间记录；已由 13.1.3 Sol 终审通过结论覆盖）

默认 browser 隔离栈每次在内部生成测试密码与邮件 HMAC；Argon2 helper 仅以 stdin 接收密码，CLI 不再接受明文密码。外部服务运行必须在任何浏览器或网络活动前从明确 test-only 环境变量取得相应凭据。私有 receipt 和显式 RAG report 均以 non-reparse、exclusive-create 路径排除仓库、默认 `data/`、`DATA_ROOT` 和 `DATA_HOST_ROOT`；默认 receipt/evidence 随 owned temp 删除，清理失败为非零。

owned API、worker、Web、browser child 和 recovery runner 在 Windows 使用本轮专属 kill-on-close Job Object；仓库内 gate bootstrap 必须先以 `-I -S -B` 隔离启动并被 assignment，才可放行原目标执行。bootstrap helper 必须是路径链无 reparse 的普通文件，保留环境键按 Windows 大小写不敏感语义拒绝冲突；原目标在放行后仍继承其余 caller 环境。在其他系统使用独立 session/process group；即使 leader 先退出也必须确认后代、pipe 与端口收口，不能依名称全局终止。`public_js_budget.py` 默认只允许 loopback origin，远程公开静态站点另须 `--allow-remote-public` 且不加载私有输入。`rag_eval.py` 在导入应用前禁用 dotenv；synthetic-smoke 只允许空 embedding endpoint/hash 离线 profile，acceptance 保持 `--authorized`，远程 embedding 另须 `--allow-remote-embedding`。这些额外授权门禁不改变报告 schema、指标、keyword/vector retrieval 或 acceptance 的既有判定策略；显式报告/证据仍可能含已授权 provenance 或私有 UI。真实部署、真实私有数据、Cloudflare、Lighthouse 与 M1/M2/M3 仍须独立验收。

### 13.1.3 本机 harness 最终门禁结论（2026-09-01，Sol 终审通过）

Sol 已在全新、无 reparse 的 system-temp parent 与显式 `DATA_ROOT` / `DATA_HOST_ROOT` 下独立完成本阶段验证：harness 与 recovery 90 passed（30.31s），完整 API 285 passed 加 1 条既有 Starlette warning（68.01s），`tests.e2e_smoke` exit 0，本阶段 Python Ruff `--no-cache`、Node 13 passed、`pnpm test`/`lint`/`build` 均通过；lint 为 0 errors、0 warnings、9 条既有 hints。默认隔离 browser E2E exit 0，所有默认检查无 FAIL，原始邮件归档 gate 通过；4321、8000、4173 listener 均为 0，受控 browser/evidence/mail/final-temp 临时对象均无残留。

终审前后根 `data/` 的目录 mtime 与 `repos.json`、`db`、`db-shm`、`db-wal` 的长度、mtime、SHA-256 完全一致。Sol 仅在绝对路径、system-temp 前缀和无 reparse 验证后，删除本次自有临时 parent 的 28 个条目；16 个历史 `starisle-e2e-*` 与既有 `scripts/.e2e-browser-evidence` 均未读取或清理。本结论只表示本地 harness 门禁通过；真实部署、真实私有数据/模型/账号、Cloudflare、Lighthouse 与 M1/M2/M3 仍未验收，且本阶段未安装依赖、未运行 Git、Docker、部署或外部网络。

### 13.1.4 部署拓扑安全候选最终结论（2026-09-01，Sol 终审通过）

Sol 对合成 deployment topology/config 候选完成独立终审：targeted 为 `20 passed`；`deployment_preflight` 为 local `16`、prod `16`、local-cpu `24`、prod-cpu `24`，总计 `89`；Ruff `0.15.20` 通过。完整 API 为 `305 passed` 加 `1` 条既有 warning（`70.96s`），`tests.e2e_smoke` exit `0`；`pnpm test` 的 Node `13`、contracts `5`、web `19` 均通过，lint 为 `0 errors`、`0 warnings`、`9 hints`，build 为 `10 pages`。

缺少 DATA、生产 inputs、MODEL 的三组 Compose 负例均为 exit `1`；`80/443/4321/4173/8000/8080/8081` listener 均为 `0`，preflight 自有临时对象为 `0`。根 `data/` 基线指纹完全一致；`apps/api/.env` 的长度和 mtime 未变，其内容未被人工读取或输出。

本结论只覆盖合成 configuration/topology 与隔离 preflight。它不证明 Docker build/up/run/pull/create/down、Caddy runtime 或 Caddyfile 语法已经验证；本机 Caddy CLI 未安装、未运行。真实域名/email/`PUBLIC_SITE_URL`/API 环境输入、TLS/ACME/CDN、SSE 首字节/heartbeat/cancel、worker runtime health/job smoke 和部署均未验证，M1/M2/M3 仍不得标记完成。此阶段未安装依赖、无 Git 写；Terra 曾一次只读 `git status` 范围检查，未执行暂存、提交或推送。

### 13.1.5 前端移动知识库本地候选证据（2026-09-01，Sol 终审通过）

Sol 独立终审的本地隔离候选新增单一 `KnowledgeBaseWorkspace` island：390×844 下“文档 / 问答”互斥、键盘切换、移动会话披露的 `aria-expanded`、打开焦点、Escape 关闭/恢复及底部导航无遮挡均经默认 browser E2E 观察；桌面在同一工作区同时展示两区，控件满足 44px 触摸目标，React 19 表单改为精确 `SubmitEvent`，无 citation 的完成回答有诚实提示，登录错误关联保持字段语义。`pnpm --filter @star-isle/web lint` 为 72 files、0 errors/0 warnings/0 hints；`pnpm test` 为 email-worker 13、contracts 5、web 19 全过；`pnpm build` 为 10 pages；默认隔离 `python -B scripts/browser_e2e.py` 为 12 groups/0 failures，并实际观察 upload→worker ready→RAG citation、邮件链路和错误观察。4321/4173/8000/8080/8081 listener 与最近 10 分钟新增 `starisle-*` temp 均为 0；root `data/` 四个已知文件长度/mtime/SHA 与前基线一致；`apps/api/.env` 仅以 length 1070、mtime `2026-08-27T10:19:55.2325824Z` 核对元数据，未人工读取/输出内容。本阶段无 Git、Docker、依赖安装、外部网络或部署；这只证明本地隔离候选，不代表真实域名/CDN/Cloudflare/Caddy/Docker、真实个人内容/授权媒体、真实 GGUF/20 篇/30 题、屏幕阅读器人工验收或 M1/M2/M3 完成，既有部署与模型外部边界保持不变。

### 13.1.6 生产静态制品门禁本地候选证据（2026-09-02，Sol 终审通过）

**Evidence → Finding → Path。** 新增 `scripts/release_artifact_gate.py` 及其隔离测试，根脚本增加 `release:gate` / `build:release`，README 记录生产使用方式，普通 `pnpm build` 保持开发可用。门禁仅从进程环境读取 `PUBLIC_SITE_URL`，不加载 dotenv；config-only 在构建前 fail closed，只接受 ASCII、非 IDNA、无 userinfo/端口/path/query/fragment 的生产形态 HTTPS DNS 根名，拒绝 IP、浏览器式 IP 和特殊用途域名。完整 gate 固定检查 dist 的链接、13 项必需制品、SEO/JSON-LD、private noindex、robots/sitemap、危险文件、强 secret、source map、本地 URL、源码路径、可见对象字符串与明确占位；私有 bundle `/api/v1`、`GITHUB_TOKEN` 变量名和框架 JS 诊断不误报。成功输出的是 deterministic fingerprint 与 5 项 input inventory，不是 SBOM、SCA 或签名。

Sol 证据为定向 106 passed（3.95s）、Ruff 通过、API venv 全量 411 passed + 1 条既有 Starlette warning（76.51s）；lint 为 72 files、0 errors/0 warnings/0 hints，test 为 email-worker 13/contracts 5/web 19，build 为 10 pages。无 DNS/网络的合成 syntax-only HTTPS 根配置允许普通构建后仅由 `CONTENT_PLACEHOLDER` 拦截；无 URL 的 `pnpm build:release` 在构建前 exit 1，且 `dist/index.html` 的 length、mtime、SHA-256 不变，随后无 URL 的开发 dist full gate 固定为 `CONFIG_INVALID_URL` + `CONTENT_PLACEHOLDER`。默认隔离 browser E2E 为 12 groups/0 failures；lockfile、root data 四项基线、`.env` metadata、listeners 与 temp 清理均保持既有安全边界；最初系统 Python 的 9 个缺依赖收集错误已由现有 `.venv` 的全量通过解释，未安装依赖。

该候选仍按设计拒绝发布，直到项目所有者提供真实域名、经授权真实内容和非空来源；不改或伪造内容，M1/M2/M3 未完成。未验证 DNS/TLS/Caddy/CDN/deploy/Lighthouse、真实内容授权、SBOM/SCA/signature 或 vulnerability reachability；供应链工具不可用且未安装，Docker、Git 和外网均未运行。

### 13.2 前端与契约规划命令

```bash
# 源码就位后执行的规划命令；当前未执行；Lighthouse 使用移动端默认配置
pnpm lint
pnpm test
pnpm build
pnpm exec playwright test
pnpm test:contracts
pnpm exec lighthouse http://127.0.0.1:4321
```

### 13.3 部署拓扑候选命令（仅合成配置，未部署）

```bash
# 仅检查合成 topology/config；不要由这些命令推导服务、Caddy 或部署已运行。
# deployment_preflight 的内部受控 JSON 保持不变；手工 Compose 检查前须显式设置对应变量，且不输出展开配置或 API env 内容。
# 不在本节执行 Docker build/up/run/pull/create/down。
python -B scripts/deployment_preflight.py
docker compose -f infra/compose/compose.yml -f infra/compose/compose.local.yml config --quiet --no-env-resolution
docker compose -f infra/compose/compose.yml -f infra/compose/compose.prod.yml config --quiet --no-env-resolution
docker compose -f infra/compose/compose.yml -f infra/compose/compose.local.yml -f infra/compose/compose.cpu.yml --profile cpu config --quiet --no-env-resolution
docker compose -f infra/compose/compose.yml -f infra/compose/compose.prod.yml -f infra/compose/compose.cpu.yml --profile cpu config --quiet --no-env-resolution
```

这些仅是 base + local / prod / cpu 的候选配置检查。手工 Compose 命令必须先显式设置对应变量，并以 `--quiet --no-env-resolution` 避免输出展开配置或 API env 内容；合成 `deployment_preflight` 的内部受控 JSON 不变。它们不验证 Caddyfile 语法或 Caddy runtime；本机 Caddy CLI 未安装且未运行，也没有 TLS/ACME/CDN、SSE 首字节/heartbeat/cancel、worker runtime health/job smoke 或真实部署结论。

### 13.4 隔离恢复演练命令与记录

```powershell
# 从仓库根目录执行；以下变量必须是本次演练专用的绝对路径。
# backup、target 和 report 必须使用本次演练专用的隔离路径；三者均不得与 data/ 或 DATA_ROOT 重叠，且 backup 不得包含这些运行时根。target/report 必须不存在，report 父目录必须存在。
$backup = 'D:\recovery-input\star-isle-backup'
$target = 'D:\recovery-work\star-isle-target'
$report = 'D:\recovery-reports\star-isle-drill.json'
'一条已知会命中候选知识库的非敏感查询' | & .\apps\api\.venv\Scripts\python.exe -B .\scripts\recovery_drill.py --backup $backup --target $target --report $report --knowledge-query-stdin
```

只可给 runner 已完成的 format v3/schema v5 候选。它需要至少一张照片、至少一份 ready 知识文档和与 target DB 精确匹配的单一可用 `sqlite-vec` profile；本地未配置 embedding endpoint 时可使用 `hash-v1`，远程 profile 则要求 endpoint 与 model 已配置且可用。profile 不匹配、endpoint/model 不可用或零检索命中均不得降级为成功；runner 不启动服务，也不会把空数据、关键词回退或伪造模型结果视为成功。查询仅由 stdin 接收，stdout/report 均不得记录查询或私有内容。失败时必须保留并人工检查 `<report>.star-isle-recovery-drill-incomplete`、target/report partial 和可能的 `<report>.star-isle-recovery-drill.tmp`，确认均为本次隔离路径后再显式清理，不能复用或覆盖。

验收记录必须保存命令版本、环境 profile、输入样本、预期/实际结果与失败处置；不要用“命令执行过”代替明确的断言。该命令仅是 isolated local candidate 的恢复证据，不能用作真实语料、真实模型/profile、M2 或生产恢复完成的声明。

## 14. 准备门槛与逐周执行计划

### 14.1 准备门槛（2026-08-27 至 08-31，不计入十周）

| 时间 | 目标 | 输出 | 验收门禁 | 裁剪规则 |
| --- | --- | --- | --- | --- |
| 08-27 至 08-28 | 冻结目标、范围、隐私和许可证边界 | 本文、前端专项和高层规划的相互链接与版本关系 | 三文档无冲突的架构、阶段和 API 根路径 | 仅保留 Must 模块描述，视觉扩展延期 |
| 08-29 | 准备内容与配置决策 | 公开内容清单、secret 清单、数据保留选择记录 | 无真实 secret、用户名、域名或私人数据进入计划文本 | 暂不决定未知域名/硬件，改由配置项表达 |
| 08-30 至 08-31 | 建立实施入口决策 | Monorepo、静态托管、备份与验收证据的开始条件 | 明确一期只选 Cloudflare Pages 或 GitHub Pages 其一 | 若账户条件未定，仅推进本地内容与测试设计 |

### 14.2 十周节奏

每周目标约 20 小时。若当周实际可投入少于 15 小时，先执行第 2.4 节裁剪，不把未完成任务叠加到新模块。

| 周次 / 日期 | 约 20 小时任务包 | 可交付物 | 验收门禁 | 范围裁剪 |
| --- | --- | --- | --- | --- |
| W1 / 09-01 至 09-06 | `apps/web` 基础、内容集合、设计令牌、首页/项目/简历结构、仓库快照契约 | 可本地构建的公开门户骨架与版本化仓库缓存策略 | 公共页无私有 API 依赖，内容与仓库 schema 已审查 | 延期动效、细节主题和语言占比条 |
| W2 / 09-07 至 09-13 | 背景、播放器进度续播、SEO、404、单一静态托管选择与 M1 回归 | M1 静态门户候选版本 | 移动端 Lighthouse 四项均不低于 90；仅自有/授权/公版音频 | 保活增强失败时保留进度续播 |
| W3 / 09-14 至 09-20 | FastAPI 骨架、Cookie 鉴权、CSRF、SQLite 迁移、Caddy 同域计划 | 私有 API 契约与登录最小闭环 | 未授权端点返回 401/403；JWT 不落 `localStorage` | 延期控制台视觉与非必要状态卡 |
| W4 / 09-21 至 09-27 | StorageAdapter、asset/photo、上传/下载/删除、缩略图和安全校验 | 文件与照片最小闭环 | 路径、MIME、大小、像素、配额负例被拒绝 | 延期虚拟滚动和高级相册布局 |
| W5 / 09-28 至 10-04 | Email Worker、HMAC、nonce、Message-ID、MIME/HTML 清洗、收件箱基础 | 邮件接收候选链路 | 合法测试邮件 60 秒内可见；重放与危险 HTML 被处理 | 延期未读体验、标签和 IMAP |
| W6 / 10-05 至 10-11 | Caddy 联调、同源浏览器流程、备份与恢复演练、M2 回归 | M2 私有服务候选版本与恢复记录 | 健康检查、未授权拒绝、对象/SQLite 恢复通过 | 延期高级部署自动化和性能仪表盘 |
| W7 / 10-12 至 10-18 | CPU/GPU profile 选择、双 `llama-server` 内部连通、VectorStore 接口、job 表 | RAG 基础运行环境和评测集骨架 | 模型端口不公开，30 题评测集结构已冻结 | 先 CPU 跑通，GPU 优化延期 |
| W8 / 10-19 至 10-25 | PDF/Markdown/TXT 解析、chunk/embedding、`sqlite-vec`、索引状态/重建 | 至少 20 篇文档的入库候选 | 文档、chunk、vector 引用可追踪；失败任务可重试 | 延期重排，不引入 OCR/Qdrant |
| W9 / 10-26 至 11-01 | `POST /rag/query` 流、引用、会话、取消、提示注入边界 | RAG 问答候选闭环 | 每个展示回答包含引用；注入样本不改变权限 | 延期悬浮入口与复杂历史管理 |
| W10 / 11-02 至 11-08 | 离线评测、CPU/GPU响应记录、恢复演练、M3 回归、文档维护记录 | M3 候选版本与验收包 | CPU 小于 60 秒、GPU 小于 10 秒量级；引用和恢复通过 | 保留基本检索，延期重排/界面精修 |

## 15. 里程碑验收矩阵与 Definition of Done

| 里程碑 | 验收维度 | 通过定义 |
| --- | --- | --- |
| M1 | 公共体验 | 移动端 Lighthouse Performance、SEO、Accessibility、Best Practices 均不低于 90 |
| M1 | 仓库展示 | 分页快照可追溯；`featured` 本地配置不被写成 Profile pinned；无 Token 进入浏览器产物 |
| M1 | 本地交互 | 背景只在同设备同浏览器持久化；播放器至少支持进度续播；音频版权边界明确 |
| M2 | 鉴权 | 未授权私有 API 返回 401/403；短 JWT 仅在 Cookie；CSRF/Origin 违规请求拒绝 |
| M2 | 上传 | 伪装 MIME、路径/符号链接、过大文件、过大像素和超配额等负例不产生可访问对象 |
| M2 | 邮件 | 合法测试邮件 60 秒内可查看；HMAC 时窗、nonce、Message-ID 和 HTML/远程图片控制有效 |
| M2 | 恢复 | SQLite 与对象关联在隔离恢复演练中可验证 |
| M3 | 语料与检索 | 至少 20 篇文档入库；30 题离线评测有版本和结果记录 |
| M3 | 问答 | 回答附带可定位引用；`fetch + ReadableStream` 可处理阶段、心跳、引用、完成与取消 |
| M3 | 性能 | CPU 单次响应目标小于 60 秒；GPU 单次响应目标小于 10 秒量级，均注明硬件/profile |

**Definition of Done。** 任一功能只有在以下条件都满足时才可标记完成：范围与本文一致；实现和配置不含真实 secret；自动化或手工验收有明确输入与断言；失败路径被记录；文档链接、契约和迁移说明同步更新；日志不泄露私有内容；相关备份/恢复影响已检查。若只完成界面、命令或单一 happy path，状态只能是进行中。

## 16. 风险登记

| 风险 | 概率 | 影响 | 触发信号 | 缓解 | 应急 | 责任角色 |
| --- | --- | --- | --- | --- | --- | --- |
| 算力不足 | 中 | 高 | CPU 延迟超过目标、模型无法稳定加载 | 小模型、Q4、profile 记录、先离线评测 | 保留检索与引用，延期生成体验或升级硬件评估 | 项目所有者 |
| 范围蔓延 | 高 | 高 | 当周增加新模块、低于 15 小时仍并行推进 | Must/Should/Could 与裁剪顺序 | 冻结新功能，回到最近未通过门禁 | 项目所有者 |
| 空仓库起步成本 | 高 | 中 | 基础骨架/忽略规则占用功能周 | 准备门槛先定边界、最小实现切片 | 延期 Could，不缩减安全门禁 | 实施者 |
| API 限流或来源故障 | 中 | 中 | 构建抓取 429/5xx、缓存过期 | 分页、缓存、Token 仅 CI/服务端 | 显示最后成功缓存和采集时间 | 实施者 |
| 邮件滥用或重放 | 中 | 高 | 异常大小、重复 nonce、签名失败增长 | HMAC、窗口、幂等、限额、清洗 | 暂停入口、保全最小审计、轮换 secret | 实施者 |
| 音乐/图片版权 | 中 | 中 | 无法确认授权、公开传播范围扩大 | 仅自有/授权/公版，必要时登录后可见 | 移除相应媒体并保留无媒体功能 | 项目所有者 |
| 隐私泄露 | 中 | 高 | GPS、日志、对象路径或 Cookie 出现在公开面 | 默认隐藏 GPS、随机 ID、脱敏、Cookie 保护 | 暂停暴露面、轮换密钥、审查日志和备份 | 项目所有者 |
| pre-v1 向量依赖变动 | 中 | 中 | `sqlite-vec` 兼容性或性能回归 | VectorStore、评测基线、数据版本 | 达阈值后评估 Qdrant，保留重建能力 | 实施者 |
| 备份不可恢复 | 中 | 高 | 只有备份成功日志，没有恢复证据 | 每里程碑演练、对象和数据库一致性验证 | 停止破坏性迁移，先修复恢复链路 | 实施者 |
| 许可证不合规 | 低 | 高 | 引入不明复制代码或资产 | 来源登记、许可证审阅、AGPL/GPL 仅行为参考 | 替换实现并补充审阅记录 | 项目所有者 |

## 17. 决策门与后续扩展阈值

| 决策 | 允许触发条件 | 未达条件时的默认 |
| --- | --- | --- |
| `sqlite-vec` → Qdrant | chunk 超过 50,000，或检索 p95 超过 300 ms，或稳定并发超过 3，且离线评测/运维成本支持迁移 | 继续 `sqlite-vec`，保持 VectorStore 和可重建索引 |
| SQLite → PostgreSQL | 出现多用户需求、持续写锁、无法接受的运维恢复需求，且迁移方案通过演练 | 保持单用户 SQLite 与一致性备份 |
| 静态托管 → VPS/Caddy 同域入口 | 二期私有 API 已就绪，需要同域 Cookie 与反代；并已确认备份、TLS、健康检查 | 一期只保留一个静态主机，不运行常驻后端 |
| 增加 Admin UI | 命令行/最小受保护界面无法安全完成必需运维，并有明确角色/审计需求 | 使用受控单用户流程，不创建复杂后台 |
| 增加博客/评论 | M1-M3 均验收且连续容量有余 | 不进入十周范围 |
| 增加 IMAP | Worker 不满足账户/域名条件，并完成凭据保管、延迟、错误恢复评估 | 仅使用 Cloudflare Email Worker 验收路径 |
| 增加 OCR | 文档需求明确依赖扫描件，且资源、隐私和误识别处理通过独立评估 | 仅支持 PDF、Markdown、TXT |

未达到门槛不得为了“完整感”提前引入对应服务、依赖或界面。

## 18. 来源与访问日期

所有外部资料为本计划的来源定位，不代表本次工作重新联网检索。访问/核验日均为 2026-08-27。

- [manuelernestog/astrofy](https://github.com/manuelernestog/astrofy)

- [satnaing/astro-paper](https://github.com/satnaing/astro-paper)

- [fastapi/full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template)

- [fastapi-users/fastapi-users](https://github.com/fastapi-users/fastapi-users)

- [mickael-kerjean/filestash](https://github.com/mickael-kerjean/filestash)

- [immich-app/immich](https://github.com/immich-app/immich)

- [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp)

- [asg017/sqlite-vec](https://github.com/asg017/sqlite-vec)

- [qdrant/qdrant](https://github.com/qdrant/qdrant)

- [caddyserver/caddy](https://github.com/caddyserver/caddy)

- [webbertakken/email-worker](https://github.com/webbertakken/email-worker)

- [navidrome/navidrome](https://github.com/navidrome/navidrome)

- [llama-server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

- [GitHub REST repositories documentation](https://docs.github.com/en/rest/repos/repos)

- [Caddy automatic HTTPS documentation](https://caddyserver.com/docs/automatic-https)

- [Cloudflare Email Worker documentation](https://developers.cloudflare.com/email-service/api/route-emails/email-handler)

- [本地历史输入：星屿StarIsle-个人全栈门户项目计划书.docx](./星屿StarIsle-个人全栈门户项目计划书.docx)

## 19. 附录

### 19.1 未来命令清单

第 13 节列出的前端、后端、Compose、健康检查和恢复命令均为源码就位后执行的规划命令。第一份实现提交应把每条命令映射到真实的工作区、输入夹具和验收记录；不应为了让文档“看起来完整”而在空仓库中伪造输出。

### 19.2 术语

| 术语 | 本计划中的含义 |
| --- | --- |
| 同域 | 浏览器访问静态页面与 `/api/v1` 使用同一站点来源，Cookie 不依赖宽泛跨域策略 |
| StorageAdapter | 将业务对象 ID 与具体本地卷/对象存储实现隔离的接口边界 |
| durable job | 写入 SQLite 后可在进程重启后继续处理的任务记录 |
| `featured` | 本地配置明确选出的仓库展示顺序，不是 GitHub Profile pinned 的替代声明 |
| CSRF | Cookie 会话下防止跨站伪造状态变更的 token 与 Origin 校验组合 |
| HMAC | Worker 与 API 间对规范化请求进行完整性与来源认证的共享密钥签名 |
| RAG | 先检索经过授权入库的文本片段，再生成带引用回答的流程 |
| `sqlite-vec` | 计划中的嵌入式向量检索起步方案，明确处于 pre-v1 |

### 19.3 文档维护规则

- 高层规划、本文和前端专项必须维持相互链接；架构、API 根路径、阶段日期或安全模型变化时先更新本文，再同步两个下游文档。

- 每次实际实现后只记录经过验证的结果，区分“计划”“已实现”“已验证”“已部署”；不得将路径、命令、示例响应或候选依赖写成现状。

- 每次来源快照更新要记录日期、许可证、stars、pushed 和借鉴边界。许可证变动、维护状态变动或依赖升级应重新进入决策门。

- 执行记录不得包含真实密钥、私人邮箱正文、完整 Cookie、JWT、HMAC、私人文件内容或未经授权的媒体。
