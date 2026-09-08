---
name: 热点新闻获取
version: 1.0.0
category: ''
description: 检查并获取今日热点日报（output 目录已存在则读取，不存在则生成）
tags: []
---

# 今日热点获取

## 工作流

1. 调用 `filesystem__list_directory`，path 填 output 目录，查找是否已有今日热点文件（文件名含今天日期，如 `hot_daily_YYYYMMDD.md`）
2. 若已存在 → 调用 `filesystem__read_file` 读取内容，直接返回
3. 若不存在：
   a. 调用 `weibo__get_weibo_hot` 获取微博热搜 Top10
   b. 调用 `weibo__get_zhihu_hot` 获取知乎热榜 Top10
   c. 整理为 Markdown 格式（排名、标题、热度、摘要）
   d. 调用 `filesystem__write_file`，path 填 `hot_daily_YYYYMMDD.md`，写入 output 目录
   e. 返回热点内容摘要
