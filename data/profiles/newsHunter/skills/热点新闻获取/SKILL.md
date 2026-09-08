---
name: 热点新闻获取
version: 1.0.0
category: ''
description: 检查并获取今日热点日报（output 目录已存在则读取，不存在则生成）
tags: []
---

# 今日热点获取

## 工作流

1. 调用 `web_search` 搜索今日热点新闻（关键词示例：「今日热点 微博热搜 知乎热榜 2026年9月8日」）
2. 汇总多个来源，整理为 Markdown 格式（分类 + 排名 + 标题 + 热度 + 一句话摘要）
3. 调用 `write_file` 写入 `output/hot_daily_YYYYMMDD.md`
4. 返回热点内容摘要
