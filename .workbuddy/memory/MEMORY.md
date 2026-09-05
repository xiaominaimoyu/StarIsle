# StarIsle 项目长期笔记

## 验证命令（本机可用路径）
- API 测试：`./apps/api/.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider apps/api/tests`（venv 在 apps/api/.venv）
- 全链路冒烟：`cd apps/api && ./.venv/Scripts/python.exe -B -m tests.e2e_smoke`
- 前端：`pnpm --filter @star-isle/web test|lint`（lint = astro check）、`pnpm build`、`pnpm --filter @star-isle/contracts test`
- ruff：系统 PATH 有全局 ruff（venv 内没有）
- **browser_e2e 必须用** `C:\Users\ASUS\AppData\Local\Python\pythoncore-3.14-64\python.exe -B scripts/browser_e2e.py --headless`——这是唯一带 playwright 的解释器；用错解释器会快速失败为 BROWSER_E2E_FAILED

## 已知既有问题（非回归，勿重复排查）
- `test_recovery_drill.py::test_popen_parse_failure_reaps_sibling_runner_without_waiting_for_default_timeout`：
  2026-09-04 实测已从「偶发」变为**必现失败**（单跑 5/5 fail）。根因是用例只给子进程首行输出
  `timeout_seconds=0.1`，而 `spawn_owned_process`（scripts/harness_safety.py:679）要先起一次
  `python -I -S -B owned_process_bootstrap.py` 做进程归属 = 两次解释器启动；本机 venv python
  冷启动 0.83–0.89s。属测试时序预算过紧，非被测逻辑缺陷。修复需独立批次，不得 skip
- **本环境 `pnpm build` 首次可能假失败**：sandbox safe-delete 代理无法清理
  `apps/web/node_modules/.vite/deps`（Vite 可再生缓存），报 `[safe-delete] 操作失败`，exit 1。重试即通过
- browser_e2e"无失败请求"项：Vite dev 导航期 abort 噪声，经 A/B 对照证明与代码无关；harness 设置阶段间歇性 30s 健康等待超时

## 项目约定
- schema 版本 fail-closed 纪律：当前 v5；任何索引/表结构变更 = 版本迁移，会涟漪 backup_restore.SUPPORTED_SCHEMA_VERSIONS、recovery_drill、测试版本哨兵。2026-09-03 所有者决策：audit 索引不做 v6，留待独立批次
- 保留期默认值须经所有者确认（audit=90 天已确认；mail/idempotency=30 天沿用约定）
- 维护任务模式：单事务 BEGIN IMMEDIATE 有界批量删除 + audit 旁路（仅聚合计数）+ observability 白名单 JSONL + worker run_maintenance 故障隔离块
- audit_event 会被维护任务自身的旁路行写入，测试计数须排除对应 action
- 进度记录：`docs/开发进度.md` 追加新节 + 更新顶部"最新实时快照见第N节"；格式含批次快照表（编号/完成时间/变更摘要/验证结果/关联文件）
- 禁止：读取/修改 `.env`、仓库根 `data/`；未经批准执行 Git 操作

## 2026-09-03 完成批次
- 批次1（§38）：photos 游标分页 + 修复 4 端点 nextCursor off-by-one 既有缺陷
- 批次2（§39）：idempotency 保留期清理（IDEMPOTENCY_RETENTION_DAYS=30）
- 批次3（§40）：audit_event 90 天保留 + 10 万软容量 + 5 分钟限速窗口保护 + Compose 三服务日志轮转
- 批次4（P2：conversation/message 分页、job cancel、观测一致性）未启动，待用户决定

## 2026-09-04 状态
- §42 P1 基线收敛（auth/observability/mail F401+Compose 示例+邮件旁路）、§43 P2 4A 会话/消息游标分页（本地候选）
- §45 为最新代码库扫描快照（项目概况/实现进度/遗留问题/质量状态/近期动向）；顶部指针已指向 §45
- 现状：28 条 API 路由、schema v5（18 业务表 + schema_version，11 索引）、源码 34,172 行
- 遗留占位：`auth.py:102` 死导入（noqa 抑制）、`ConsoleOverview.tsx` 存储/作业两卡 P2 4C 未接线、
  `apps/web/src/data/` 死目录（已迁 site-data/）、`scripts/debug_kb_403.py` 调试脚本在仓
- 未忽略产物 15 项（`scripts/.e2e-browser-evidence/` 13 + `.workbuddy/` 2），首次 Git 前须人工审核
- 覆盖盲区：29 个 .tsx 组件零单测、`services/mime.py` 无直接测试、前后端均未配置覆盖率工具
