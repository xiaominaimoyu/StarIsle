# 星屿 StarIsle - 叙事体验层开发设计

> **状态：首个本地原型已实现；后续阶段仍为 Proposed。** 2026-09-05 已实现 `/enter` 的静态壳、语义封印按钮、SVG 融化、独立状态、减弱动效与无脚本可用的 `/`、`/login` 真实链接，并完成单元、构建和隔离浏览器回归。真实认证、音频、Console 撕裂、后续素材与跨文档连续体验仍未实现；上述本地证据不等同于部署或生产验收。
>
> **日期：** 2026-09-05（UTC+8）  
> **范围：** 规划一个可选择进入的信封、火漆、登录信件与“撕开前往星”的前端体验；不改变既有公开门户、私有控制台、认证、API、安全或里程碑。  
> **结论摘要：** 推荐新增 opt-in 的 /enter 入口（方案 B），让 / 始终保持现有公开 Portal。首个最小原型只做 /enter 的静态壳、语义封印按钮、SVG 融化、独立状态与减弱动效；不连接真实认证、音频、Console 或根页。

## 1. 文档定位与现有关系

本文服从 [星屿 StarIsle - 总体具体开发计划](./星屿%20StarIsle%20-%20总体具体开发计划.md) 的跨模块执行基线，服从 [星屿 StarIsle - 前端开发计划](./星屿%20StarIsle%20-%20前端开发计划.md) 的技术、路由和阶段约束，并只在视觉/体验层细化 [前端设计文档](./设计文档.md) 已有的“星与屿”意象。它不覆盖上述文档的认证、API、Cookie/CSRF、数据模型、发布门禁或 M1/M2/M3 定义。

旧计划顶部的“空仓库 / Planned / Not implemented”是 2026-08-27 的历史基线，而非当前源码事实；当前进度应以 [开发进度](./开发进度.md) 的最新统一快照为准。§38–§45 仅保留各自发生时的历史状态，不能覆盖 2026-09-05 的代码与复验证据。静态代码和本地测试都不能被表述成生产验收；M1、M2、M3 均未完成。

### Evidence → Finding → Path

| Evidence | Finding | Path |
| --- | --- | --- |
| Astro 配置明确为 static，Base 在非 noindex 页面生成 canonical，根页输出 JSON-LD，sitemap 只列公开路径。 | / 的 SEO、JSON-LD、canonical 与 sitemap 是既有公开 Portal 的边界，叙事层不得改写或吞掉它们。 | apps/web/astro.config.mjs:10-28；apps/web/src/layouts/Base.astro:25-46；apps/web/src/pages/index.astro:19-46；apps/web/src/lib/seo.ts:58-90 |
| 登录页以 LoginForm client:load 挂载；SessionGate 先取 CSRF、后校验/刷新会话；唯一 API 客户端使用同源 /api/v1、credentials include 和 X-CSRF-Token。 | 叙事 UI 不能成为认证真相源，不得存 JWT，也不得直接绕开既有登录与 SessionGate。 | apps/web/src/pages/login.astro:1-19；apps/web/src/components/auth/LoginForm.tsx:21-84；apps/web/src/components/auth/SessionGate.tsx:16-65；apps/web/src/lib/api/client.ts:3-55 |
| Base 默认挂公共背景和 PublicMusicPlayer；Console 使用 chrome=false；PublicMusicPlayer 是原生 Astro 脚本，playlist 当前为空；另有 React MusicPlayer 与 player nanostore 文件。 | 真正挂载路径是 Base → PublicMusicPlayer。React MusicPlayer/player store 不是当前音频运行时真相，不能新建竞争播放器。 | apps/web/src/layouts/Base.astro:1-77；apps/web/src/layouts/Console.astro:1-76；apps/web/src/components/player/PublicMusicPlayer.astro:19-27、92-390；apps/web/src/site-data/playlist.json:1-3；apps/web/src/components/player/MusicPlayer.tsx；apps/web/src/stores/player.ts |
| apps/web/src 内未检出 ClientRouter、astro:transitions 或 transition:persist；内部链接是普通 a。 | 现在是完整文档导航；跨文档 BGM 连续性不能当成低风险承诺。 | apps/web/src 的只读文本扫描；apps/web/src/layouts/Base.astro；apps/web/src/layouts/Console.astro |
| 开发进度的最新统一快照区分代码存在、本地验证、部署与生产验收；旧总体/前端计划仍保留 2026-08-27 空仓库警告。 | 设计可参考现有源码，但任何“已达成”都必须回到最新快照的证据边界。 | docs/开发进度.md；docs/星屿 StarIsle - 总体具体开发计划.md；docs/星屿 StarIsle - 前端开发计划.md |

## 2. 设计目标

- 为二维码、个人名片和主动到访者提供克制的、可跳过的叙事入口，同时使公开分享仍直接到达 /。
- 让“星”准确指向现有 Public Portal，让“屿”准确指向现有 /console/*；叙事只是到达方式，不发明 /star 或 /isle 路由。
- 以静态 HTML、明确导航和 React island 的最小水合保持 Astro 静态站点特性。
- 在不改变登录安全链的前提下，把信封、火漆、信件、撕裂与音乐设计成可降级、可取消、可测试的浏览器体验。
- 使无 JavaScript、Canvas、SVG、Web Audio 或媒体权限的访问者仍可读到内容、前往 / 并正常使用 /login。
- 将公共首屏、键盘、触摸、减弱动效、强制高对比度和移动端性能视为交付约束，而不是末尾装饰。

## 3. 非目标

- 不自动把 / 重定向到 /enter，不替换既有 Portal，不把全站改为 SPA。
- 不新增 /star、/isle、FantasyMusicPlayer、认证端点、session store 写入、JWT 存储、私有数据预取或私有 DOM 复制。
- 不承诺跨完整导航的样本级无缝 BGM；不以动画为由引入 ClientRouter 或 transition:persist。
- 不在本轮获得、下载、嵌入或宣称拥有任何图像、纹理、音乐或音效。所有未来素材都必须是自有、获得授权或公版，并在合入前记录来源与许可。
- 不把图像快照作为唯一门禁，不把本地候选、静态检查或历史测试升级为生产验收。

## 4. 现有架构约束

- 当前锁文件解析的 Astro 安装候选为 5.18.2；配置为 output: static。本文不把 Astro 6 专属行为当作已适用于本项目的事实。
- / 是现有公开 Portal；/console/* 是现有 Isle。/login 的 LoginForm client:load 继续负责真实登录，/console/* 的 SessionGate 继续在后端事实之上重验会话。
- 统一同源 /api/v1、credentials include、CSRF 与 Secure/HttpOnly Cookie 是不可变边界；客户端只保存最小 session 摘要，不存 JWT（也绝不把它保存在浏览器存储中）。
- Base 默认挂 PublicBackgroundLayer 和 PublicMusicPlayer，Console 以 chrome=false 关闭它们。新增 /enter 必须显式使用 Base chrome=false，避免无意加载公共背景/播放器。
- 当前全局 prefers-reduced-motion 已把动画降至近即时；叙事 JavaScript、Canvas 和 SVG 也必须自行监听变化并释放资源，不能假定 CSS 已覆盖脚本工作。
- 公共根页既有初始 JavaScript 目标为不高于 60KB gzip。入口动效不能侵蚀该目标；/enter 的成本必须单独报告。
- 触摸可操作控件的目标不低于 44×44 CSS px；撕裂把手留在左侧偏内，以回避 iOS 边缘返回手势。

## 5. Narrative Experience Layer 总体架构

方案 B 由一个静态 Astro 壳、一个可水合的 NarrativeEntry island、两个 route-local 状态机、只读事件桥和每个活动 document 一个框架无关 AudioManager 构成。静态壳在 island 外输出标题、说明、前往星和普通登录链接；因此不使用 client:only，也不把导航依赖交给 JavaScript。

~~~mermaid
flowchart TD
  Browser["Browser"] --> Shell["/enter static Astro shell"]
  Shell --> Island["NarrativeEntry React island"]
  Island --> EnterState["/enter route-local state machine"]
  Bridge["Event bridge: facts only"] --> Island
  Island --> EnterAudio["AudioManager for this document"]
  Browser --> Portal["/ public Portal document"]
  Portal --> PortalAudio["AudioManager for this document"]
  Browser --> Console["/console document"]
  Console --> TearLink["TearPortalLink island"]
  TearLink --> ConsoleState["/console route-local tear state machine"]
  TearLink --> ConsoleAudio["AudioManager for this document"]
  PortalAudio --> Adapter["PublicMusicPlayer adapter when mounted"]
  Login["Existing LoginForm + CSRF"] --> Api["/api/v1 backend"]
  Gate["Existing SessionGate/session subsystem"] --> Api
  Gate --> Summary["Narrow credential-free session summary"]
  Summary --> Bridge
~~~

完整 document 导航是运行时边界：/enter 的 island 在跳往 /console 时销毁，/console 的 TearPortalLink 在该文档重新实例化；两者之间没有连续、可信的 narrative runtime。普通静态链接始终不依赖状态机。

信任边界：Narrative 只消费 route、可公开的会话摘要、local music 状态、visibility 与 motion 等无凭据事实；它不能回写 session store、不能授予 authenticated、不能访问 Cookie 内容。登录的成功或失败仍来自既有 API；进入 /console 后仍由 SessionGate 重验。

## 6. Narrative State Machine

建议在独立 narrative nanostore 或纯状态机中保存界面状态，禁止与 stores/session.ts 合并。当前完整 document 导航下存在两个独立机器；所有状态仅描述展示，不代表认证、不会跨文档持久，也不能授予会话：

~~~mermaid
stateDiagram-v2
  [*] --> idle
  idle --> sealed: render
  sealed --> melting: seal activate
  melting --> loginReveal: melt complete
  loginReveal --> loginReveal: login failure
  loginReveal --> isle: existing login reports success
  isle --> [*]: full navigation to /console
~~~

上图只属于 /enter。它在完整导航开始时结束，没有一条状态边连接到 /console 的机器。

~~~mermaid
stateDiagram-v2
  [*] --> isleIdle
  isleIdle --> tearing: TearPortalLink primary pointer
  tearing --> isleIdle: cancel or rollback
  tearing --> starCommit: threshold complete
  starCommit --> [*]: location.assign("/")
~~~

上图只属于已加载的 /console/* 文档。TearPortalLink 是其独立轻量 island；它不是 /enter NarrativeEntry 的后续状态，也不使用户获得或失去认证。

| 路由局部状态 | 状态 | 视觉职责 | 不允许的推论 |
| --- | --- | --- | --- |
| /enter | idle、sealed、melting、loginReveal、isle | 信封、火漆和登录信件；isle 只是一段短 acknowledgement。 | 不能表示已读、已登录或媒体已解锁；不能拦截静态 /、/login 链接。 |
| /console/* | isleIdle、tearing、starCommit | 只管理 TearPortalLink 的回门户手势。 | 不能抓取 / 的 DOM、预取私有内容或替代 SessionGate。 |

撕裂数据另存，不混进有限状态：

~~~ts
// Proposed pseudo-code; not directly runnable.
type EnterNarrativeState =
  | "idle" | "sealed" | "melting" | "loginReveal" | "isle";

type ConsoleTearState = "isleIdle" | "tearing" | "starCommit";

type TearGesture = {
  progress: number;
  velocity: number;
  acceleration: number;
  pointerId: number | null;
};
~~~

## 7. Envelope Scene

/enter 的 shell 建议以 Base chrome=false 渲染。静态输出至少包括页面标题、简短说明、可见的“前往星”链接（/）和“普通登录”链接（/login）；它们不在 overlay 内，也不依赖 island 成功水合。NarrativeEntry client:load 只增强信封、火漆和登录信件表现，初始 SSR HTML 必须仍含语义 seal button。

信封的视觉层次依次为：羊皮纸表面、折页、内信、火漆 base、rim、imprint、residue 与少量 magic。组件应将装饰层标为 aria-hidden，保留真实标题、说明、主操作与备用链接的普通文档顺序。封印可用 button；“前往星”始终为 a href="/"。

建议新增以下辅助 token，但保持现有 ink、star、glow 名称和值不变。所有色值是候选，实施前必须以实际文字、焦点、边界和 forced-colors 回退做对比度测试：

~~~css
/* Proposed candidate tokens; not implemented. */
:root {
  --color-parchment: #ead9b9;
  --color-parchment-shadow: #a78661;
  --color-wax: #9f3b4b;
  --color-wax-dark: #54202d;
  --color-ember: #e39a4c;
  --color-magic: #9bb7ff;
  --color-fiber: #f6ead0;
}
~~~

## 8. Wax Seal Melt

封印主体采用 SVG；仅 seal 的小范围可使用 filter。火漆应拆为 base、rim、imprint、residue 和 magic 五层，避免把一张不可访问的位图作为唯一内容。

- feTurbulence 使用静态 seed，numOctaves 不超过 2；手势或时间轴期间不得改变 seed。
- feDisplacementMap 只作用于 seal 或窄边缘；动态 scale 低频更新，不高于 30Hz。
- Gaussian blur 约不超过 1.2 CSS px，filter region 紧贴实际图形；不使用现行规范已移除的 filterRes。
- 高质量允许静态纤维和较完整 residue；中质量关闭大部分滤镜更新；低质量以 CSS opacity/scale 和静态 SVG 代替。magic 粒子不超过 12。
- 色彩只允许暗金、月白、低饱和蓝与极少量紫罗兰点缀；所有亮度都以火漆本体和印痕为光源锚点。禁止 bloom、爆炸感、频闪、高密粒子、MMORPG 风格或霓虹综合色。

| 阶段（原型候选） | 物理表现 | 参数驱动 | 声音钩子（仅有授权素材时） |
| --- | --- | --- | --- |
| 0–0.35 秒 ignition | 纹路内先亮起暗金/月白，边缘仍稳固。 | imprintLuma 从 0 平滑到低值；lightAnchor 固定在 seal。 | 单个极轻 ignition tick。 |
| 0.35–0.9 秒传播 | 亮度沿印痕传播，低饱和蓝只作冷边缘。 | revealLength 随时间单调增长；不改 turbulence seed。 | 很低的短 hum，不做持续轰鸣。 |
| 0.9–1.7 秒软化 | 蜡体失去硬边、出现不规则边缘的软化和局部下坠。 | soften、edgeDisplace 低频更新，不高于 30Hz。 | soften 纹理短音，可为静音。 |
| 1.7–2.25 秒坍塌 | 液态拖尾向下坍塌，内信露出，残蜡不规则。 | gravityBias、tailLength 与 residueOpacity 平滑变化。 | 不超过一次柔和 release。 |
| 2.25–2.8 秒 settle | 留下暗红 residue 与清晰信件，不再扩散。 | 所有动态量归零或定格。 | 停止或极短尾音。 |

2.4–2.8 秒是候选时间轴；减弱动效改为 120–150ms 的简化状态切换。所有参数、颜色、声音钩子都需要授权素材和真机实测调参，视觉即使完全无声也应成立。

## 9. Login Integration

登录能力只复用现有 LoginForm、其 API 模块、CSRF 获取、401/429 文案和站内 redirect 规则。未来可在 NarrativeEntry 内增加 presentation label 或成功回调 adapter，使信件外观承载现有表单；不得复制 submit 逻辑、不得改变默认 redirect、不得创建第二条 auth 请求链。

初始焦点移到信件标题或说明，不强制把焦点塞进密码框。登录失败保持 loginReveal 并呈现既有错误；成功可以显示极短的 isle 视觉，但随后仍以完整导航进入 /console，由 SessionGate 重验。首个最小原型不接真实认证，普通登录链接是唯一登录出口。

## 10. Isle/Star关系

“星”严格等同于现有 Public Portal（/），“屿”严格等同于现有私有 /console/*。这不是新的信息架构，而是两个已存在区域的叙事命名。

在已加载的 /console/* 中由 TearPortalLink 发起撕裂时，背后先展示 StarBackdropPreview：它只能是纯装饰的星空/色块/轮廓，不抓取、复制或水合 / 的 DOM，不请求公开构建数据，更不预取任何私有内容。阈值完成后调用 location.assign("/")；真实 URL、刷新、历史记录和深链接因此保持浏览器原生语义。

Console 的回门户入口可在未来用独立轻量 TearPortalLink island 增强，或继续使用语义 a href="/"。它不得引入 Envelope、Wax 或整套 NarrativeEntry bundle 到 console 或根页。

## 11. Tear Interaction

TearPortalLink 位于 /console/*，撕裂手柄是左侧偏内的 48–56px 语义 button/anchor 区域，说明文字通过 aria-describedby 关联，并始终提供“前往星”普通链接。手势为水平左→右；元素使用 touch-action: pan-y pinch-zoom，允许垂直滚动和缩放。/enter 的第一原型没有撕裂；后续隔离 spike 只能拥有自己的本地原型状态，不可把 sealed 当作正式手势回滚目的地。

以下均为**原型候选、需实测调参**：

~~~ts
// Proposed pseudo-code; not directly runnable.
rawProgress = clamp(dx / travel, 0, 1);
displayProgress = resistance(rawProgress); // pure function, at most one frame behind
velocity = clamp(ema(dx / dt), -2, 2);     // timestamp delta
complete = progress >= 0.62 || (progress >= 0.45 && velocity >= 0.8);
~~~

- 轴锁 slop 为 10–12px；完成阈值、EMA 系数与 travel 均须按触摸/鼠标实际数据调节。
- pointerdown 只接受唯一 primary pointer，立即 setPointerCapture，并登记 pointerId、起点与时间；这避免指针在 slop 内离开命中区而丢失事件。axis lock 仍在 10–12px 后区分水平/垂直意图：垂直意图不驱动 progress，由 touch-action 与 pointercancel 协调浏览器滚动；pointermove 只接受已捕获的同一 pointerId。
- pointerup 按阈值完成或回滚；pointercancel、lostpointercapture、Escape、resize、visibility 切换和第二根手指均取消并回滚。
- 回滚候选为 220–320ms，完成候选为 180–320ms。reduced motion 下不跟随连续位移，而是保留可点击前往星和 120–150ms 状态切换。
- 每帧同一 progress 驱动裂缝、上下纸片 transform、Star 曝光、边缘纤维、粒子和声光；不让不同层各自推导互相矛盾的进度。

## 12. SVG Tear Architecture

撕裂必须组合三类分工：CSS clip-path/polygon 负责宏观上下纸片分离与 transform；一个 deterministic、归一化的 irregular SVG path 是唯一规范几何，生成 points、path 和 cumulative length；SVG mask 控制从左到 tip 的渐进开裂与背景曝光。feTurbulence 加 feDisplacementMap 只施加在窄 edge band，形成轻微纸纤维，不承担整体撕裂形状。

几何路径只生成一次，手势期间不改变 seed。路径 sampler 同时提供粒子发射位置、切线和法线，因此 CSS、mask、Canvas 与声音不会使用各自随机的裂缝。桌面高质量可显示窄边缘置换和更多离散采样；移动低质量保留同一几何与 mask，但停用动态滤镜、降低采样和粒子数。

实现时必须显式设置 maskUnits、maskContentUnits、filterUnits、primitiveUnits 与紧贴图形的 filter region，并对 alpha/luminance、linearRGB/sRGB 做跨浏览器视觉核对；本文不预设或虚构任何一种模式已经验证优于另一种。

~~~ts
// Proposed pseudo-code; not directly runnable.
type TearGeometry = {
  points: ReadonlyArray<{ x: number; y: number }>;
  path: string;
  cumulativeLength: ReadonlyArray<number>;
  sampleAt: (progress: number) => {
    point: { x: number; y: number };
    tangent: { x: number; y: number };
    normal: { x: number; y: number };
  };
};
~~~

任何滤镜 benchmark 都须先在静态 geometry 上做；Safari/iOS、低端移动设备与 forced-colors 都必须可退回无滤镜的清晰轮廓。

## 13. Canvas Debris System

Canvas 是透明的 pointer-events:none Canvas 2D，仅在首次真实撕裂时懒初始化。粒子总量为 20–40：桌面 32–40，移动 20–24，reduced motion 为 0。发射点沿新增长的真实 path segment，而非屏幕中心。

~~~ts
// Proposed pseudo-code; not directly runnable.
type Debris = {
  x: number; y: number; vx: number; vy: number; gravity: number;
  rotation: number; angularVelocity: number; width: number; height: number;
  scale: number; opacity: number; depth: number; age: number;
  lifetime: number; flipPhase: number;
};
~~~

法线方向按上/下 paper side 决定；cosine 与 scaleX 模拟翻转。动画以 requestAnimationFrame 的时间戳积分，生命周期候选为 2–2.5 秒；出界或透明粒子立即回收。对象池可以在 benchmark 显示分配抖动后再引入，不能预优化。

Canvas 必须使用 ResizeObserver，DPR 候选上限为 1.5；Page Visibility hidden 时暂停。unmount 时 cancel rAF、移除监听，且将 canvas width/height 设为 0 释放 backing store。连续 20 次进入/取消后不得有增长中的 canvas、rAF、listener 或 AudioNode。

## 14. Audio Architecture

建议引入框架无关的 AudioManager：**每个活动 document 一个 singleton**，且每个 singleton 只复用一个 AudioContext。它提供 master、BGM、Local Music、SFX 四个总线：同一 document 内的 BGM 才可使用两套长音频 media deck 加 GainNode 做真正的 equal-power 交叉淡入淡出；SFX 解码为可复用 AudioBuffer，每次触发新建 AudioBufferSourceNode。

PublicMusicPlayer 通过 adapter 幂等注册它当前的 audio 元素，仍保留现有 UI 与空歌单状态。adapter 必须避免对同一 element 重复 createMediaElementSource；React MusicPlayer 和 stores/player.ts 不被假定为音频真相，它们若未来继续存在，也只能通过同一个 adapter 表达状态。不得创建 FantasyMusicPlayer、第二个 AudioContext 或第二套播放 UI。

两个 BGM deck 的真实 crossfade 候选为 1.5–3 秒，默认候选 2.2 秒；此说法只适用于同一 document 内仍存活的两个 deck。媒体 preload=none，按用户手势或明确播放需要加载/解码。音效只在已有可授权素材后接入，视觉在没有音频时仍应完整。

### Isle/Star 音乐方向与导航策略

| 区域 | 明确的 Proposed 声音意图 | 候选声音层 | 循环与许可边界 |
| --- | --- | --- | --- |
| Isle（/console/*） | 私人、温暖、近场，像书房与岛屿灯火。 | 壁炉、细雨或远海浪三选一为主；钢琴、竖琴或低音弦只作稀疏旋律层。 | 低密度、克制、适合循环；环境声与乐器不全量叠满。 |
| Star（/） | 辽阔、夜空、旅行与空气感。 | 夜风或远钟二选一为主；笛/弦为轻旋律，极轻 Choir 仅作远景点缀。 | 低密度、克制、适合循环；不把氛围层堆成持续满响。 |

以上是 Proposed 的情绪与分层方向，不是已有曲目、素材或自动播放承诺。每一层都只能使用自有、授权或公版素材；没有相应许可时应静音/省略，而不是以替代来源补满。

| 范围/方向 | 当前 document 中可做的事 | 完整导航边界后的策略 | 禁止表述 |
| --- | --- | --- | --- |
| 同一 document 的场景切换 | 两 deck 做 1.5–3 秒 equal-power overlap crossfade。 | 不涉及导航。 | 不能把该能力外推到 /console↔/。 |
| Isle → Star（/console→/） | TearPortalLink 开始时可对本页 BGM/SFX 短淡出。 | 写入非敏感 handoff；/ 新 document 仅在用户策略/手势允许时按需淡入，可能有 gap。 | 不能称为真正重叠 crossfade。 |
| Star → Isle（/ 或 /enter→/console） | 当前页如有用户已启用的声音，可先短淡出。 | /console 新 document 只在策略允许时按需淡入或保持静音；SessionGate 仍独立重验。 | 不能借 handoff 代表登录成功或恢复私有媒体。 |

sessionStorage handoff 仅可含公开 BGM scene key、归一化 position、归一化 gain、timestamp 等非凭据数字/标识。它绝不包含本地文件路径、曲名、私有媒体元数据、token、用户资料或任何 session 数据。样本级无缝仍需要持久 document/ClientRouter，是 Future 决策。

### Gesture audio mapping

| 输入 | clamp/EMA 与平滑 | 可驱动的克制声音 | 上限/取消 |
| --- | --- | --- | --- |
| velocity | 对时间戳 delta 的 EMA 后 clamp。 | gain 由低到中、playbackRate 候选 0.85–1.25、预制 friction loop 的密度档位。 | AudioParam smoothing；更新不高于 30Hz。 |
| progress | clamp 到 0–1，避免回弹噪声。 | loop presence 与 low-pass 候选 1.2–6kHz。 | 不新建每帧节点；取消时短淡出。 |
| acceleration | EMA 后仅作阈值型强调，避免抖动。 | 很轻的 friction accent 或密度微调。 | 不承诺复杂实时合成；更新不高于 30Hz。 |
| completion | 来自状态阈值，而非连续加速。 | 一次 snap/completion transient。 | 一次性触发；失败/取消不重放。 |

所有 loop 都是授权的预制短素材或静音退化，而非承诺复杂合成；cancel 统一对相关 gain 做短淡出。

## 15. BGM / Local Music Priority

| 情况 | Local Music | BGM | 处理 |
| --- | --- | --- | --- |
| 初始页面、无手势 | 不自动播放。 | 不自动播放。 | 页面保持静音。 |
| 用户显式播放本地曲目 | 允许。 | 若原本在播，duck 到 -12dB（gain 约 0.251）。 | attack 候选约 250ms。 |
| 本地曲目暂停或结束 | 已暂停。 | 仅当 BGM 原本已开且用户未静音时恢复。 | recovery 候选约 600ms。 |
| 用户已静音或 BGM 原本没开 | 可按本地播放器意图处理。 | 不自动恢复。 | 永不双满音量。 |

master mute 与 bgm/sfx 偏好分离；local music 仍由已有播放器的显式操作决定。任何音量/偏好保存都不能包含会话、token、Cookie 或私有路径。

## 16. Autoplay Strategy

首个可听声音必须在 seal button 或 tear 控件的同步 pointer/click handler 内创建或 resume AudioContext，并立即尝试触发首个 SFX；不要先 await。随后捕获 resume/play 的 Promise 拒绝，并将失败降级为无声视觉，而不是再强制弹出“开启声音”门。

- 直接进入 / 或 /console 且没有手势时保持静音；封印、撕裂和现有播放器都可成为合法解锁手势。
- 处理 AudioContext 的 suspended、interrupted、closed 和 statechange；closed 只能重建管理器，不得保留失效节点。
- tear loop 的 gain、playbackRate、lowpass 和 transient 遵循 §14 的 clamp、EMA、AudioParam smoothing 与每秒不超过 30 次更新；均需按授权素材实测。
- cancel 使用短淡出，complete 播放 snap；wax 使用 ignition、hum、soften 的短序列。没有对应授权素材时全部静音。
- 跨完整导航只允许 §14 定义的 best-effort sessionStorage handoff：公开 BGM scene key、归一化 position/gain、timestamp；可能有 gap 或被浏览器拦截。它不携带曲名、本地路径、私有媒体元数据、token、用户或 session 数据。样本级无缝需要持久 document/ClientRouter，是 Future 决策，不属于 Must。

## 17. Routing / First Visit Strategy

推荐方案 B：新增 opt-in /enter，使用 Base chrome=false 的静态壳和 noindex；它不进入 sitemap，核心文字与两个链接静态输出，不使用 client:only。二维码/个人名片可指向 /enter；公开分享始终指向 /。/ 不自动跳转，也不读写叙事偏好。

| 维度 | 方案 A：首次 / overlay + 本地偏好 | 方案 B：opt-in /enter | 决议 |
| --- | --- | --- | --- |
| SEO | root 首屏与 crawl 内容容易被 overlay 干扰。 | / 保持既有 canonical、JSON-LD 与 sitemap；/enter noindex。 | 推荐 B。 |
| 无障碍 | overlay、焦点、回退和返回访问需要额外风险控制。 | 普通入口不受影响，叙事页可独立审计。 | 推荐 B。 |
| UX | 首访有仪式感，但可能打断直接求职访问。 | 自愿进入，分享与普通访问都清晰。 | 推荐 B。 |
| 加载 | / 增加初始 JS、样式和失败面。 | / 的 Narrative 初始 JS 增量目标为 0。 | 推荐 B。 |
| 路由复杂度 | 要维护首次判断、回退、历史与 overlay 生命周期。 | 仅新增标准静态页面与链接。 | 推荐 B。 |
| 回访 | 依赖偏好键和例外策略。 | 用户按链接选择是否再次进入。 | 推荐 B。 |
| 分享 | 链接语义混合。 | / 为公开分享，/enter 为主动叙事入口。 | 推荐 B。 |
| 成本 | root 回归、SEO、性能和 a11y 成本高。 | 独立成本、可 feature-flag、可整页回滚。 | 推荐 B。 |

方案 A 仅列为 Could：只有方案 B 通过 Lighthouse、无障碍与回访验证后才评估。若实施，偏好键建议为 star-isle:narrative:intro-seen:v1，所有 localStorage 读写都用 try/catch，且仅在用户完成或明确跳过后写入；该偏好只是体验状态，绝不是认证或授权状态。

Phase 1 的 feature flag 只能控制叙事入口的公开暴露/灰度，不得成为安全门禁。无论 flag 状态如何，/ 永远保持普通公开 Portal；被关闭的增强须退回静态导航，不得产生死链。

## 18. Accessibility

- seal 与 tear 使用原生 button 或 a；键盘 Enter/Space 等价触发，且可见的“前往星”后备链接永远存在。
- 把焦点移到信件标题或说明；不强制聚焦密码框。overlay 打开时正确使用 focus containment 和 inert，关闭时返还触发点；skip link 继续可用。
- 撕裂手柄有文字标签和 aria-describedby；live region 只播“封印已打开”“已显示登录信件”“已前往星”等里程碑，不播每帧进度。
- forced-colors/high contrast 下用静态边框、文字和轮廓，不依赖颜色、滤镜、纤维或粒子传达操作意义；浏览器缩放不得裁掉信件或链接。
- audio mute、无声失败、SVG 失败、Canvas 失败和 JavaScript 失败均不阻断 / 或 /login。reduced motion 不等于静音，音频偏好独立。
- 手势结果在 pointerup 完成或可以撤销，满足可取消语义；鼠标、触摸、笔和键盘均有等价路径。

## 19. Reduced Motion

叙事层在 CSS 与 JavaScript 两侧订阅 prefers-reduced-motion，并在 media query 变化时取消 rAF、短路连续指针渲染、停用 Canvas 粒子、停用动态 SVG filter，并清理旧 listener/AudioParam automation。现有全局样式已经把动画降为近即时；本设计不把这一全局规则误当作 Canvas、Web Audio 或脚本的自动清理。

减弱动效模式保留语义顺序：seal button 可操作，120–150ms 状态切换后显示登录信件，tear 以点击/键盘导航替代连续撕裂，粒子数为 0。用户的音频选择、静音与既有播放器控制不因该偏好被改变。

## 20. Responsive/Mobile

质量分级不用 UA sniff。判定顺序为：reduced motion 与 saveData 是硬降级；随后综合 viewport、可选 deviceMemory、首次短窗口实际帧率和运行中掉帧；任一信号变差可下调，不自动上调到会破坏稳定性的 tier。

| Tier | 候选触发 | 信封/火漆 | Tear/Canvas | 音频 |
| --- | --- | --- | --- | --- |
| 高 | 非 reduced、非 saveData、宽视口或实际帧率稳定，且可选 memory 信号充足。 | 小范围滤镜、最多 12 magic 粒子。 | edge band 滤镜；32–40 debris。 | 30Hz 以下参数更新。 |
| 中 | 未满足高 tier，但无硬降级。 | 静态纹理和低频火漆更新。 | 同一几何，关闭多数动态滤镜；20–24 debris。 | 保留用户触发的简短 SFX。 |
| 低 | saveData、低帧率、很窄视口或可选 memory 信号不足。 | CSS/SVG 静态外观。 | 无动态滤镜，最多 20 debris。 | 可完全无声但不妨碍导航。 |
| 减弱动效 | 用户明确 reduced motion。 | 120–150ms 简化切换。 | 0 debris、无连续撕裂。 | 音频独立于动效偏好。 |

移动端把手为左侧偏内 48–56px，最小可触达区域不低于 44×44px；避免占用系统边缘返回区。布局在 200% 及更高缩放时仍按正常文档流显示信件、登录链接和前往星链接。

兼容性验证候选按引擎而非虚构版本号记录：Chromium current/current-1、Firefox current/current-1、Safari macOS current/current-1、Safari iOS current/current-1。实现前冻结最低支持版本，并在实际 Safari/iOS 设备上验证 pointer capture、audio resume、滤镜退化和页面导航。

## 21. Performance Budget

以下全部为 Proposed，必须用真实授权素材、目标构建产物和代表性设备实测，不能写成已达成：

| 项目 | 目标/上限 | 测量边界 |
| --- | --- | --- |
| / 的叙事初始 JS 增量 | 0 | 方案 B 不让 NarrativeEntry 进入根页。 |
| /enter 专用 Narrative JS | 不高于 75KB gzip | 含专用交互；共享 React 单独报告。 |
| console tear lazy chunk | 不高于 30KB gzip | 不得把 envelope bundle 引入 console。 |
| 初始化主线程任务 | 不高于 50ms | 中档移动设备，按真实 profile 记录。 |
| 帧率 | 高档 60fps、低档不低于 30fps | 质量 tier 与实际 dropped-frame 数据共同判定。 |
| 三秒交互漏帧 | 少于 5% | seal、tear、cancel 三类样本分别记录。 |
| seal 过滤表面 | 约不大于 180×180 CSS px，更新不高于 30Hz | 只测真正滤镜区域。 |
| 粒子与 Canvas | live 不超过 40，DPR 不超过 1.5 | hidden 暂停、释放 backing store。 |
| 音频 | preload=none、按需 decode | 不以自动播放换取预热。 |
| 生命周期 | 20 次进入/取消后无递增资源 | rAF、listener、canvas、AudioNode 都采样。 |

公开根页 Lighthouse 四项仍以既有 M1 目标均不低于 90 为准；本文不声称该目标已经达到，也不把 /enter 的分数混入 / 的发布门槛。

## 22. Component Architecture

| 建议组件/模块 | 单一职责 | 禁止承担 |
| --- | --- | --- |
| NarrativeEntry | 信封、火漆、状态机编排和登录信件表现。 | 认证真相、API 请求重写、全局路由。 |
| EnvelopeScene / WaxSealSvg | 语义信封 DOM 与 SVG 分层视觉。 | 私有数据、媒体自动播放。 |
| TearPortalLink | 仅用于 Console 的“屿→星”回门户增强。 | 用于 Portal 的“星→屿”、加载完整 envelope/wax bundle。 |
| StarBackdropPreview | 撕裂背后的纯装饰。 | 复制 / DOM、预取 public/private 内容。 |
| tear geometry lib | 一次生成路径、points、长度和 sampler。 | 自行管理会话、音频或 DOM。 |
| narrative store | 展示状态与 tear 数据。 | 写 session store、JWT、授权结果。 |
| AudioManager | 单一 AudioContext、总线、SFX 与 media deck。 | 新建独立玩家 UI、保存凭据。 |
| PublicMusicPlayer adapter | 将已挂载 audio 元素接入管理器。 | 替换当前播放器、强制播放。 |

事件桥只接收 route、无凭据 session summary、local music 事实、visibility 与 motion；它必须是窄接口。Narrative 到 auth/session 的唯一方向是展示层回调，不能反向设置 authenticated。

Portal 侧若需要“星→屿”，继续使用既有链接/登录流程与可选的非敏感音频 handoff；它绝不挂载或复用 TearPortalLink。

## 23. Suggested Directory Structure

下列所有路径均为**建议、未创建**；它们贴合当前 apps/web/src/site-data、现有 lib/stores 和同目录 *.test.ts 习惯，不采用旧计划中不存在的 feature 目录。NarrativeEntry 保持 /enter 的唯一完整叙事 island；TearPortalLink 只在 Console 的“屿→星”回门户增强中另成轻量 island，避免无必要碎片。Portal 的“星→屿”继续走既有链接/登录与可选音频 handoff，不使用该撕裂组件。新增依赖、测试框架配置或素材目录须另行批准。

~~~text
apps/web/src/
  pages/
    enter.astro                         # 建议、未创建：Base chrome=false 静态壳
  components/
    narrative/
      NarrativeEntry.tsx                # 建议、未创建
      EnvelopeScene.tsx                 # 建议、未创建
      LoginLetter.tsx                   # 建议、未创建：仅 LoginForm 的呈现容器
      WaxSealSvg.tsx                    # 建议、未创建
      WaxMeltEffect.tsx                 # 建议、未创建：仅在拆分确有必要时
      TearMask.tsx                      # 建议、未创建：SVG mask 层
      PaperDebrisCanvas.tsx             # 建议、未创建：透明 Canvas 2D
      TearPortalLink.tsx                # 建议、未创建，独立 lazy island
      useTearGesture.ts                 # 建议、未创建：对 controller 的最小 React hook 包装
      StarBackdropPreview.tsx           # 建议、未创建
  lib/
    narrative/
      state-machine.ts                  # 建议、未创建
      state-machine.test.ts             # 建议、未创建，纯逻辑同目录测试
      gesture-controller.ts             # 建议、未创建：唯一 primary pointer 控制器
      gesture-controller.test.ts        # 建议、未创建，纯逻辑同目录测试
      tear-geometry.ts                  # 建议、未创建
      tear-geometry.test.ts             # 建议、未创建，纯逻辑同目录测试
      quality.ts                        # 建议、未创建
      quality.test.ts                   # 建议、未创建，纯逻辑同目录测试
      preferences.ts                    # 建议、未创建：仅体验偏好
      preferences.test.ts               # 建议、未创建，纯逻辑同目录测试
      event-bridge.ts                   # 建议、未创建：无凭据事实输入
      event-bridge.test.ts              # 建议、未创建，纯逻辑同目录测试
    audio/
      AudioManager.ts                   # 建议、未创建
      types.ts                          # 建议、未创建：公开 scene/handoff 类型
      audio-test-helpers.ts             # 建议、未创建：测试用 mock/helper
      AudioManager.test.ts              # 建议、未创建，纯逻辑/adapter 测试
      public-player-adapter.ts          # 建议、未创建
  stores/
    narrative.ts                        # 建议、未创建，不能修改 session.ts
  site-data/
    narrative-copy.ts                   # 建议、未创建，静态文案/标识
apps/web/public/
  narrative/
    manifest.json                       # 建议、未创建：未来公开素材清单
    music/                              # 建议、未创建：仅授权公开 BGM
    sfx/                                # 建议、未创建：仅授权公开 SFX
    textures/                           # 建议、未创建：仅授权或公版纹理
LICENSES/
  narrative-assets.md                   # 建议、未创建：许可与来源记录
~~~

现有 apps/web/src/site-data 下的 featured.json、playlist.json、repos.json 保持原职责。当前 playlist 为空，任何 BGM/SFX 文件或清单只有在授权素材到位并获批后才可新增。

## 24. Event / State Flow

~~~mermaid
sequenceDiagram
  participant User
  participant Entry as NarrativeEntry
  participant EnterAudio as EnterAudioManager
  participant Login as ExistingLoginForm
  participant Browser
  participant Gate as SessionGate
  participant Api as ExistingApi
  participant Tear as ConsoleTearPortalLink
  participant ConsoleAudio as ConsoleAudioManager
  User->>Entry: seal click
  Entry->>EnterAudio: synchronous resume or first SFX attempt
  Entry->>Entry: sealed to melting to loginReveal
  User->>Login: existing submit flow
  Login->>Browser: existing redirect after real success
  Browser->>Browser: destroy /enter then load /console
  Browser->>Gate: mount protected console route
  Gate->>Api: CSRF/session recheck
  Api-->>Gate: session result
  Gate-->>Browser: render console content or redirect /login
  Browser->>Tear: mount only after authorized console render
  User->>Tear: primary pointer tear interaction
  Tear->>ConsoleAudio: capped gesture SFX updates
  Tear->>Browser: location.assign("/")
  Browser->>Browser: full document navigation
~~~

| 事件来源 | 可传递字段 | 允许结果 | 禁止结果 |
| --- | --- | --- | --- |
| 路由/导航 | pathname、visibility、导航意图。 | 更新展示或做普通 location.assign。 | 劫持 auth、伪造深链接。 |
| Session bridge | 匿名/已验证的摘要标签。 | 改变文案或短视觉。 | 写入 sessionStatus、CSRF 或用户资料。 |
| PublicMusicPlayer adapter | playing、muted、公开 BGM scene、归一化 position/gain 的非凭据事实。 | duck/恢复 BGM 或写入有限 handoff。 | 新建竞争播放器、重复 source node、传递曲名/本地路径/私有元数据。 |
| motion/quality bridge | reduced、saveData、尺寸、实时帧率。 | 降级特效。 | 覆盖用户音频静音选择。 |
| Console TearPortalLink pointer controller | progress、velocity、acceleration、pointerId。 | 撕裂渲染和 capped SFX。 | 从 /enter 发起正式 tear、忽略 cancel、多指或 Escape。 |

## 25. Error / Cancellation / Degradation

| 触发 | 安全降级 | 必须清理/保持 |
| --- | --- | --- |
| JavaScript 未加载或 island 抛错 | 静态标题、说明、前往星和普通登录仍可用。 | 不隐藏 shell 链接。 |
| SVG/filter 不可用或性能低 | 静态轮廓、CSS opacity/transform。 | 保持文本/控件对比与焦点。 |
| Canvas 初始化失败 | 不显示 debris。 | 不阻断 tear 或导航。 |
| Audio resume/play/decode 失败 | 完整无声视觉。 | 捕获 Promise，不循环弹窗。 |
| pointercancel/lostcapture/Escape/resize/多指 | 回滚到手势来源状态；正式 Console 场景为 isleIdle，隔离 spike 可回到其本地静态态。 | release capture、停止 loop、短淡出 SFX；不一律回滚 sealed。 |
| visibility hidden | 暂停 rAF 和 Canvas。 | 不把状态误判为完成。 |
| 登录 401/429/网络错误 | 留在 loginReveal，复用现有错误。 | 不伪造 isle，不改既有冷却/CSRF。 |
| 完整导航开始 | 记录非凭据 handoff 的 best effort。 | 不承诺无缝，销毁本页资源。 |

## 26. Testing Strategy

当前可见前端测试只有 3 个 Vitest 纯逻辑文件：public-ui.test.ts、seo.test.ts、lib/api/modules.test.ts；浏览器路径是 Python Playwright harness scripts/browser_e2e.py。没有现成 Testing Library、MSW、JavaScript Playwright 配置或相应 dev dependency。若要安装其中任一依赖，必须先单独获得批准；本文不把它们写成已安装。

| 层级 | 必测范围 | 通过条件 |
| --- | --- | --- |
| Unit | state machine、完成阈值、EMA clamp、resistance 纯函数、geometry deterministic 输出、sampler、tier 判定、AudioManager 的优先级和无凭据 handoff 编解码。 | 同输入得同路径；cancel/rollback 无越界状态；不访问 session store。 |
| Component | SSR 静态文本/两个链接、seal 的 button 语义、键盘、aria-describedby、focus、live 里程碑、forced-colors 回退、LoginForm adapter 不改默认行为。 | 不以视觉快照为唯一门禁；无 JS 路径可导航。 |
| E2E | /enter noindex、无 sitemap 条目、/ 不跳转、seal→loginReveal、普通登录链接、tear→/、/console 仍经 SessionGate、cancel/多指/resize/Escape、静音与自动播放拒绝。 | 用现有 Python harness 扩展或经批准的新配置，覆盖 Chromium 与实机 Safari/iOS。 |
| Performance | 资源体积、主线程、fps、三秒漏帧、DPR、滤镜区、hidden 暂停、20 次循环泄漏。 | 真实构建与目标设备记录，满足 §21 才能提升 tier。 |
| Accessibility | 键盘、读屏公告、焦点圈、200% 缩放、forced-colors、reduced motion、44px 触摸目标、点击/拖动可取消。 | 自动检查加人工验证；任一关键路径失败即不能灰度。 |

兼容测试在实现前冻结最低版本；矩阵只写 Chromium、Firefox、Safari macOS、Safari iOS 的 current/current-1 候选，不虚构已验证的版本或支持率。Safari/iOS 必须在实机上覆盖 pointer capture、Web Audio 恢复、SVG filter 降级、媒体导航和安全区。

### 官方资料与规范边界

访问日期均为 2026-09-03。以下仅使用第一方资料；它们提供 API/规范边界，不代替本项目锁定 Astro 5.18.2 和实际浏览器验证。

| 主题 | 第一方来源 | 本文使用边界 |
| --- | --- | --- |
| Pointer 输入、capture、touch-action | [W3C Pointer Events Level 3](https://www.w3.org/TR/pointerevents3/) | 指针、取消、capture 与跨输入设备设计。 |
| SVG filter | [W3C Filter Effects Module Level 1](https://www.w3.org/TR/filter-effects-1/) | 滤镜原语与不使用已移除 filterRes 的边界。 |
| 可访问性 | [W3C WCAG 2.2](https://www.w3.org/TR/WCAG22/) | 键盘、触摸取消、焦点、对比与可操作性目标。 |
| Autoplay | [MDN Autoplay guide](https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Autoplay) | 用户手势、播放 Promise 与静音降级。 |
| Web Audio | [MDN Web Audio API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API) | AudioContext、AudioNode、总线与参数平滑。 |
| Visibility | [MDN Page Visibility API](https://developer.mozilla.org/en-US/docs/Web/API/Page_Visibility_API) | hidden 暂停与恢复策略。 |
| Canvas/SVG | [MDN Canvas API](https://developer.mozilla.org/en-US/docs/Web/API/Canvas_API)；[MDN SVG](https://developer.mozilla.org/en-US/docs/Web/SVG) | Canvas 生命周期与 SVG 退化。 |
| 减弱动效 | [MDN prefers-reduced-motion](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-motion) | CSS/JS 对 motion 偏好的响应。 |
| Astro islands | [Astro v5 framework components](https://v5.docs.astro.build/en/guides/framework-components/)；[Astro directives reference](https://docs.astro.build/en/reference/directives-reference/) | client:load 与静态 shell 的 island 边界；实现时须对照锁定 5.18.2 验证。 |

## 27. Development Phases

每个阶段独立灰度、可撤回，且始终保持既有 /、/login 与 /console 可运行。下面保留原实施顺序；截至 2026-09-05，Phase 0 与 Phase 1 的最小原型已经形成本地候选，Phase 2 以后仍是未获实现授权的建议。

### Phase 0：事实与门禁冻结

**目标：** 冻结 §1 事实、许可边界、预算和验收记录格式。  
**允许修改范围：** 仅经批准的设计/证据文档。  
**不修改范围：** 源码、配置、路由、依赖、素材、认证。  
**依赖：** 当前开发进度 §46 与源码复核。  
**验收：** 事实与旧计划历史基线不互相冒充。  
**测试：** 文档链接、标题层级、证据路径静态检查。  
**回滚：** 撤回新增设计文档即可，现站零运行时影响。

### Phase 1：/enter 静态壳、feature flag 与 fallback

**状态：** 2026-09-05 已完成最小原型；后续素材与阶段仍不在此状态声明内。  
**目标：** 新建 noindex /enter、Base chrome=false、静态说明和 /、/login 链接。  
**允许修改范围：** 建议 pages/enter.astro 及最小静态文案/灰度暴露点。  
**不修改范围：** /、robots/sitemap 语义、登录实现、Console、API。  
**依赖：** Astro 5.18.2 静态构建语义与发布入口决定。  
**验收：** 无 JS 仍可读、前往星、普通登录；/ 不跳转；/enter 不进 sitemap。  
**测试：** 静态 HTML、robots/meta、链接、键盘和移动缩放。  
**回滚：** 隐藏入口/QR 并移除 /enter，不碰根页。

### Phase 2：state、preferences 与 accessibility

**目标：** 接入独立状态机、可选体验偏好与完整焦点/公告策略。  
**允许修改范围：** 建议 stores/narrative.ts、lib/narrative 与 NarrativeEntry。  
**不修改范围：** stores/session.ts、API client、Cookie/CSRF、现有 redirect。  
**依赖：** Phase 1 静态 fallback 已通过。  
**验收：** 状态不代表认证；偏好 try/catch 且只在完成/跳过后写入。  
**测试：** Unit 状态转换、键盘、focus、live region、forced-colors。  
**回滚：** 删除增强 island，静态壳继续工作。

### Phase 3：wax visual

**目标：** 加入分层 SVG 火漆与质量 tier。  
**允许修改范围：** 建议 EnvelopeScene、WaxSealSvg、叙事样式和纯质量工具。  
**不修改范围：** 公共设计 token 既有 ink/star/glow 值、全局播放器、认证。  
**依赖：** 无侵权素材或纯程序化形状，以及 Phase 2 的 a11y。  
**验收：** 过滤区域/频率符合 §8，reduced motion 为简化切换。  
**测试：** SVG fallback、性能 benchmark、对比度与 Safari/iOS 预检。  
**回滚：** 保留语义 seal button，替换为无滤镜静态 SVG。

### Phase 4：existing login integration

**目标：** 在信件中复用已有 LoginForm 的表现层，不改 auth 逻辑。  
**允许修改范围：** NarrativeEntry 的 presentation adapter 和已批准的登录嵌入位置。  
**不修改范围：** LoginForm 提交、API modules、SessionGate、CSRF、redirect 规则。  
**依赖：** Phase 2/3 通过及既有登录回归基线。  
**验收：** 失败留在 loginReveal，成功后 /console 仍由 SessionGate 重验。  
**测试：** 401、429、CSRF、网络错误、站内 redirect 和无 JS 普通登录。  
**回滚：** 信件只保留 /login 链接，完全移除嵌入 adapter。

### Phase 5：AudioManager 与 active player adapter

**目标：** 接入一个 AudioContext、总线和当前 PublicMusicPlayer 的 adapter。  
**允许修改范围：** 建议 lib/audio、adapter 与 PublicMusicPlayer 的最小注册点。  
**不修改范围：** 新播放器 UI、React player 作为真相、自动播放、素材许可边界。  
**依赖：** 授权测试音频、现有 player 行为基线和 Phase 4 完成。  
**验收：** 单 Context、无双满音量、失败静音、不变更现有播放控制。  
**测试：** adapter mock、duck/recovery、mute、closed/suspended、资源清理。  
**回滚：** 注销 adapter，恢复当前原生 Astro 脚本播放器。

### Phase 6：静态 tear geometry/mask benchmark

**目标：** 实现一次生成的 path、mask 与静态性能基准。  
**允许修改范围：** 建议 lib/narrative/tear-geometry、SVG 层和同目录测试。  
**不修改范围：** pointer 交互、Canvas、导航、音频。  
**依赖：** Phase 3 的 SVG 分层和目标浏览器样本。  
**验收：** deterministic path/sampler、低 tier 无动态滤镜、Safari/iOS 可退化。  
**测试：** Unit geometry、静态截图辅助、filter surface/帧率 benchmark。  
**回滚：** 回到静态完整纸张与普通前往星链接。

### Phase 7：Console pointer tear（无 debris）

**目标：** 仅在 /console/* 的 TearPortalLink 接入 pointer controller、轴锁、立即 capture、cancel 和普通导航完成。  
**允许修改范围：** 建议 TearPortalLink、gesture controller、tear geometry/mask 接口与样式。  
**不修改范围：** Canvas、音频、ClientRouter、私有预取。  
**依赖：** Phase 6 geometry/mask 测试通过。  
**验收：** isleIdle→tearing→isleIdle/starCommit 的局部状态正确；进度阈值、Escape、多指、resize、lostcapture 都可回滚。  
**测试：** 鼠标/笔/触摸、键盘、iOS 边缘、E2E 导航与 resource cleanup。  
**回滚：** 使用语义 a href="/"，不保留手势拦截。

### Phase 8：debris 与 gesture SFX

**目标：** 懒加载 Canvas debris 和 capped gesture SFX。  
**允许修改范围：** 建议 Canvas 模块、AudioManager SFX 适配、授权素材清单。  
**不修改范围：** autoplay 门、BGM 长音频切换、session、API。  
**依赖：** Phase 5、7 通过，且音效许可已核验。  
**验收：** path segment 发射、20–40 上限、30Hz 参数上限、无声完整。  
**测试：** hidden pause、DPR、20 次循环、cancel 淡出、AudioNode 不增长。  
**回滚：** 关闭 Canvas/SFX feature，保留 Phase 7 的纸张与导航。

### Phase 9：Portal/Console integration 与 BGM best-effort

**目标：** 接入 /enter、/、Console 的轻量入口和非凭据 BGM handoff，明确完整导航不是 crossfade。  
**允许修改范围：** 已批准的入口链接、TearPortalLink、PublicMusicPlayer adapter、sessionStorage handoff。  
**不修改范围：** / 的 SEO/JSON-LD/canonical/sitemap、ClientRouter、Console 认证、私有数据加载。  
**依赖：** Phase 5、7、8 的性能/资源数据和发布负责人许可。  
**验收：** / 的叙事 JS 增量为 0；handoff 只含公开 scene key、归一化 position/gain、timestamp；gap 可接受并被明确记录；console 未带 envelope bundle。  
**测试：** 根页 bundle、完整导航、静音、回访、分享 URL、SessionGate 回归。  
**回滚：** 删除入口增强和 handoff，/ 与 console 继续普通链接。

### Phase 10：performance/a11y/compat hardening 与方案 A 评估

**目标：** 固化预算、跨引擎验证、泄漏检查，并决定是否值得评估方案 A。  
**允许修改范围：** 叙事模块的性能/a11y 修正、测试和记录。  
**不修改范围：** 未获批准的全站 SPA、根页 overlay、认证/数据模型/依赖大改。  
**依赖：** Phase 1–9 的实测数据、真实素材与 Safari/iOS 验证。  
**验收：** §21、§18、§20、20 次循环与浏览器矩阵均有证据；方案 A 只有通过门槛后才可提案。  
**测试：** Lighthouse、profile、E2E、读屏/键盘、forced-colors、真实移动设备。  
**回滚：** 降回低 tier 或 Phase 1 静态入口；不影响公开根页。

## 28. Acceptance Criteria

- /enter 是可独立回滚的 noindex 静态入口，sitemap 不新增它；/ 永远保持现有 Portal、SEO/JSON-LD/canonical 和普通分享语义。
- 无 JavaScript、SVG、Canvas 或音频时，用户仍能看到静态文本并通过 / 或 /login 完成下一步。
- /enter 与 /console/* 是两个 route-local 状态机，完整导航销毁前者后再创建后者；静态链接不依赖任一状态，任何状态都不代表 auth。
- state、preference、event bridge 与 session/auth 完全隔离；没有 JWT、Cookie、CSRF、私有路径或会话写入进入叙事层。
- seal/tear 键盘可用、可取消、焦点正确、live region 克制、触摸目标不小于 44×44px、forced-colors/缩放/reduced motion 通过。
- 每个活动 document 的单例 AudioManager 与现有播放器 adapter 不产生双播放器、重复 MediaElementSource、双 Context、自动播放或双满音量；完整导航只允许淡出/handoff/按策略淡入，不称作 crossfade。
- 撕裂几何 deterministic，Canvas 受量级/DPR/visibility/生命周期约束，20 次循环没有资源递增。
- 所有 §21 预算、兼容矩阵和阶段测试都有新的可复核证据后，才允许标注相应阶段为完成；本设计本身不改变 M1/M2/M3 状态。

## 29. Risks

| 风险 | 等级 | 缓解与决策边界 |
| --- | --- | --- |
| 完整文档导航导致 BGM 间隙或恢复失败 | 高 | 只承诺 fade-out + 有限非敏感 handoff + 按策略的新页 fade-in；不称真正 crossfade，样本级无缝列为 Future。 |
| SVG filter、Canvas 和复杂路径拖慢 Safari/iOS 或低端设备 | 高 | 固定 geometry、窄 filter band、tier 降级、实机 Safari/iOS、随时退回静态。 |
| 动效破坏公开入口 SEO、可访问性或 60KB 目标 | 高 | 方案 B 隔离到 /enter；/ 的叙事增量预算为 0。 |
| 叙事层意外绕开认证或泄露会话信息 | 高 | 只读无凭据 bridge；复用 LoginForm；/console 仍由 SessionGate 重验。 |
| 授权不足的音频/纹理被误合入 | 高 | 没有素材即静音/程序化形状；合入前记录许可并获批准。 |
| 首次访问 overlay 让求职用户受阻 | 中 | 只推荐 B；A 必须通过独立 Lighthouse/a11y/回访证据后才可考虑。 |
| 手势与 iOS 边缘返回、多指、缩放冲突 | 中 | 偏内把手、touch-action、轴锁、pointer capture、cancel 及实机测试。 |
| 单例音频/Canvas 生命周期泄漏 | 中 | hidden pause、unmount 清理、20 次循环资源观察作为硬门槛。 |

## 30. Open Questions

下列事项须在对应实施阶段开始前由项目所有者决定，不能由体验实现自行推断：

1. /enter 是否在首轮灰度中对全部公开访问者可见，还是仅通过二维码/个人名片分发。
2. 火漆、纸张、BGM、SFX 是纯程序化、内部自有还是第三方授权；每项的许可记录由谁保管。
3. 登录信件是否只展示普通登录链接，还是在 Phase 4 获批准后复用现有 LoginForm 的视觉嵌入。
4. 对完整导航的可接受 BGM 间隙上限是多少；若不能接受，是否愿意承担未来持久 document/ClientRouter 的独立架构评估。
5. 哪些目标设备代表低/中/高 tier，以及 Safari/iOS 实机验证由谁提供。
6. 方案 A 是否有明确业务理由；若无，不进入实现排期。

## 31. 与现有开发计划的影响分析

| 上游/现有记录 | 保持不变 | 本文的增量 |
| --- | --- | --- |
| 总体具体开发计划 | 跨模块优先级、M1/M2/M3、部署、Cookie/CSRF、数据/安全门禁。 | 仅为浏览器叙事层提供下游设计。 |
| 前端开发计划 | Astro 静态输出、React islands、同源 API、现有路由与性能方向。 | 用 /enter 隔离体验成本，不新增 /star 或 /isle。 |
| 前端设计文档 | ink/star/glow、公开轻私有稳、内容先于装饰、双主题可达。 | 提出候选 parchment/wax/fiber 辅助 token，需对比测试。 |
| 开发进度最新 §46 | 本地候选/历史验证/生产验收的区分，M1/M2/M3 未完成。 | 不改变任何完成状态或发布结论。 |
| 现有源码 | /、/login、/console、Base、PublicMusicPlayer、SessionGate 的既有职责。 | Phase 化、可回滚的新增体验，不修改当前事实。 |

## 32. 哪些内容属于 Should / Could / Future

### Should

- 在方案 B 已经安全落地后，为 /enter 提供静态、可访问、低 tier 可用的信封/火漆体验。
- 用现有 PublicMusicPlayer 的 adapter 汇入单一 AudioManager，并在有授权素材后添加克制 SFX。
- 以 deterministic path、mask 和真实 benchmark 驱动撕裂，不把随机滤镜当作几何。

### Could

- 仅当方案 B 的 Lighthouse、无障碍和回访验证均通过后，评估方案 A 的首次 / overlay 与体验偏好键。
- 在 Console 使用独立、很小的 TearPortalLink 增强返回 / 的链接；普通 a 仍是同等可用的基础路径。
- 在用户明确接受间隙、授权素材齐备且测量证明必要时，微调 BGM crossfade 或声音映射。

### Future

- 持久 document、ClientRouter、transition:persist 或样本级跨文档无缝音频是单独的顶层架构决策，不能随动效功能捆绑引入。
- 更复杂的物理纸纤维、OffscreenCanvas、空间音频或多场景 BGM 只有在现有性能/a11y/授权门槛全绿后才可另立提案。
- 对 /enter 的 A/B 数据、回访偏好或内容个性化必须另行进行隐私、SEO 与发布评审。

## 33. 明确结论与首个最小原型

### 可直接增量

方案 B 的 /enter noindex 静态壳、Base chrome=false、静态“前往星”与“普通登录”链接、SSR 的语义 seal button、独立叙事状态和 reduced-motion 降级都可在不改变 /、/login、/console、API 或 session 的前提下分阶段增加。

### 高风险

完整文档间 BGM 连续性、动态 SVG filter、Canvas debris、手势音频和 Safari/iOS 的 pointer/audio 行为都是高风险项；它们必须后置到 geometry、性能、真实素材和实机验证之后。不能以“沉浸感”为理由让根页变 SPA 或绕开 SessionGate。

### 暂缓

方案 A 的首次 root overlay、ClientRouter/transition:persist、样本级无缝 BGM、FantasyMusicPlayer、新认证链和未获许可素材均暂缓，不进入 Must。

### 第一原型

首个最小可验证 Prototype 固定为：方案 B 的 /enter noindex 页面，静态信封、语义 seal button、SVG melt、/enter 独立状态、reduced-motion 处理，以及静态“前往星”（/）/“普通登录”（/login）fallback。它**不接真实认证、不接音频、不改 Console 或根页**。通过后，下一项风险 spike 才是隔离的静态 tear path 加 pointer controller；它先只验证本地几何/取消语义，不能把 /enter sealed 接到正式 Console TearPortalLink。Canvas、SFX、BGM 与跨页 handoff 均不在第一原型内。
