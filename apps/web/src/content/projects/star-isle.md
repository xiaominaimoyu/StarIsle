---
title: "星屿 StarIsle"
date: 2026-08-27
tags: [Astro, FastAPI, RAG, 全栈]
featured: true
sourceUrl: "https://github.com/xiaominaimoyu/StarIsle"
status: wip
---

## 项目简介

星屿是本站背后的完整项目:一个个人全栈门户。公开面用 Astro 静态生成,承担求职展示、构建期仓库快照、浏览器内自定义背景与音乐播放器;私有面用 FastAPI 提供 Cookie 鉴权的文件、照片、邮件与 RAG 知识库服务。

## 技术要点

- **公开与私有分离**:公开面无常驻后端依赖;私有 API 全部同源、Cookie 鉴权 + CSRF/Origin 校验
- **构建期数据流**:仓库快照由脚本分页抓取并写入版本化 JSON 缓存,运行时零外网请求
- **安全边界**:上传校验 MIME 魔数、大小与像素;邮件经 HMAC 签名转发、nonce 防重放、HTML 清洗
- **RAG 知识库**:已实现内部双 `llama-server` 对接接口、`sqlite-vec` 检索和流式带引用问答;离线链路已验证,真实模型与授权语料评测仍待完成

## 收获

项目已形成可本地构建和测试的工程候选,持续保留实现、验证与失败记录。公开内容、音乐授权、真实部署和模型评测仍按阶段推进,各里程碑以直接验收结果为准。
