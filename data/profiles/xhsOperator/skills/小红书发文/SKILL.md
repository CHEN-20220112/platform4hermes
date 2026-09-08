---
name: 小红书发文
version: 1.0.0
category: ''
description: 基于今日热点生成小红书笔记文案并发布（含登录引导、草稿保存、发布）
tags: []
---

# 小红书热点发文

## 前置：登录检查

1. 调用 `xhs__check_login_status` 检查是否已登录
2. 若未登录：
   a. 调用 `xhs__get_login_qrcode` 获取二维码
   b. 告知用户：请用小红书 App 扫描二维码登录，扫完回复"已扫码"
   c. 用户回复后，调用 `xhs__check_qrcode_status` 确认登录
   d. Cookie 会自动保存，后续免扫码

## 发文工作流

1. 调用 `filesystem__list_directory` 查找今日热点文件（`hot_daily_YYYYMMDD.md`）
2. 若不存在，先按 `fetch-today-hot` 流程生成
3. 调用 `filesystem__read_file` 读取热点内容
4. 基于热点撰写小红书文案：
   - 标题：选 1-3 条最热话题，组合成吸睛标题（≤20 字）
   - 正文：口语化、有情绪、带 emoji，500-800 字，分点罗列热点
   - 标签：3-5 个相关话题标签（如 #热搜 #今日热点 #社会新闻）
5. 把文案草稿保存到 `output/xhs_draft_YYYYMMDD.md`（调用 `filesystem__write_file`）
6. 准备封面图：
   - 询问用户是否已有图片，或提供图片路径
   - 若用户无图，告知必须至少 1 张图才能发布
7. 调用 `xhs__publish_content`：
   - title：标题
   - content：正文
   - images：图片路径列表（至少 1 张）
   - tags：标签数组
8. 回复用户：发布结果（成功/失败 + note_id 或错误原因）
