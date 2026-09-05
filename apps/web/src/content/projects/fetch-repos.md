---
title: "构建期仓库快照工具"
date: 2026-08-27
tags: [TypeScript, GitHub API, 工程化]
sourceUrl: "https://github.com/yourname/star-isle"
status: done
---

## 项目简介

一个构建期脚本:分页抓取 GitHub / Gitee 公开仓库,归一化为统一 schema,按本地 `featured` 清单标记精选,写入版本化 JSON 缓存供 Astro 静态页面消费。

## 设计要点

- `featured` 来自本地配置清单,不冒充平台 pinned 语义
- 来源失败时回退最近成功缓存并记录采集时间,不伪造实时数据
- `GITHUB_TOKEN` 仅存在于 CI/服务端构建环境,绝不进入浏览器产物