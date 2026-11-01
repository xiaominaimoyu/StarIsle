---
name: "沐明"
role: "全栈工程师 · AI 工程"
location: "中国"
email: "18780497725@163.com"
summary: "全栈与 AI 工程方向实践者。已实现个人门户、私有数据服务与知识库的本地工程链路,重视安全边界、可访问性与可验证的工程交付。"
---

## 项目实践

### 全栈个人项目 · 星屿 StarIsle(2026 至今)

独立设计并实现个人全栈门户,按公开展示、私有服务和知识库三个阶段持续推进:

- **公开门户**:Astro + Tailwind 展示页、构建期仓库快照、IndexedDB 自定义背景和 HTML5 播放器代码已实现;当前展示 GitHub 仓库快照,授权曲目与当前版本发布验收待补齐
- **私有服务**:实现 FastAPI + SQLite 单用户 Cookie 鉴权(Argon2id、CSRF/Origin)、文件与照片存储(魔数校验、随机对象 ID),以及 Cloudflare Email Worker → FastAPI HMAC 邮件接收参考实现;真实邮件与部署环境仍待验收
- **RAG 知识库**:实现文档索引、`sqlite-vec` 检索、带引用的流式问答和内部双 `llama-server` 对接接口;离线后端链路已验证,真实模型、语料质量与性能仍待评测

## 技能

| 类别 | 技能 |
| --- | --- |
| 前端 | TypeScript、React、Astro、Tailwind CSS、HTML/CSS 语义化与可访问性 |
| 后端 | Python、FastAPI、SQLite、REST API 设计与安全(CSRF/Cookie/限速) |
| 工程化 | pnpm monorepo、Docker Compose、Caddy、CI 构建脚本、备份与恢复演练 |
| AI 工程 | llama.cpp 本地推理、RAG 检索问答、向量检索(sqlite-vec)、流式交互 |

## 项目精选

- **星屿 StarIsle**:本网站背后的完整全栈系统(详见 `/projects`)
- **构建期仓库快照工具**:分页抓取、归一化、版本化缓存的工程实践

## 教育背景

2023.09 – 2027.06(在读)西华大学 · 智能科学与技术 · 本科

主修课程:软件工程、数据结构、操作系统原理、数据库系统原理、机器学习、Java、C++、Python。
