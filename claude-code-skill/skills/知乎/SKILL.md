---
name: 知乎
description: 爬取知乎问题或回答内容，输出为 Markdown 文件。通过 /知乎 调用，输入知乎链接即可。
---

# 知乎爬虫

爬取知乎问题（全部回答）或单条回答，输出为 Markdown 格式文件。

## 触发条件

用户提供知乎链接（`zhihu.com/question/...` 或 `zhihu.com/question/.../answer/...`），要求爬取内容。

## 执行步骤

### 1. 解析用户输入

检查用户消息中是否包含知乎链接。如果用户只给了问题 ID 或回答 ID 而没有完整 URL，从上下文补全。

### 2. 检查 cookie

读取 `{SKILL_DIR}/cookie.txt`。如果文件为空或不存在，告知用户需要提供知乎 cookie（浏览器 F12 → 复制完整 Cookie 字符串），然后写入该文件。

### 3. 执行爬取

```bash
python "{SKILL_DIR}/crawl.py" "<知乎URL>" "<输出路径>.md"
```

脚本会自动判断 URL 类型：
- 含 `/answer/` → 爬取单条回答
- 仅含 `/question/` → 爬取该问题下全部回答

### 4. 告知结果

向用户报告：爬取模式（单回答/全问题）、标题、作者/回答数、输出文件路径。如果 API 返回错误（如 cookie 过期），提示用户更新 cookie。

## 输出格式

Markdown 文件，包含：
- 问题标题（一级标题）
- 问题 ID、来源链接
- 回答内容（转换为 Markdown，保留加粗、链接、图片等）

## 依赖

- Python 3（requests 库）
- 无需 Node.js

## Cookie 更新

当爬取返回 403 或认证错误时，提示用户更新 cookie：

```
请更新 cookie：浏览器打开知乎 → F12 → Network → 找到任意 api/v4 请求 →
复制 Request Headers 中完整的 Cookie 值，粘贴到 {SKILL_DIR}/cookie.txt
```
