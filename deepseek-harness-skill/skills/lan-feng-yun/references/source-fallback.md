# 信源获取失败应对

> 本文档由原 SKILL.md「信源获取失败应对」章节迁出，并将原 L1「Claude Code 域名安全验证」改写为 DSH 环境下的真实失败模式。在 DSH 中，全文抓取通过 `pwsh` + Python `requests` 执行（无 WebFetch 工具），搜索召回与页面抓取的主要失败模式如下，策略链按优先级依次尝试。

| 层级 | 原因 | 特征 | 应对 |
|------|------|------|------|
| L0（知乎专用） | zhihu.com 反爬，但知乎有公开 API | WebSearch 返回 `zhihu.com/question/...` 链接 | `python "{SKILL_DIR}/../zhihu/crawl.py" "<URL>" "out.md"` → API 返回完整回答（文本 + `![](图片URL)` + 赞同数）。图片 URL 从返回的 md 中提取，送入 `collect_images.py` 统一下载。API 返回的图片是回答者插入的配图/截图，天然无推荐流污染 |
| L1（搜索零命中） | WebSearch 关键词过窄/过偏，召回为空 | 返回 `No results found` | ① 降级关键词：宽泛词 → 中粒度 → 精确词，零命中即回退上一档 ② 换角度/换媒体名（如公司全称↔简称、事件别名）③ 仍无命中则用 `site:`/引号限定拼合 snippet |
| L2（网络不可达/被墙） | 连接超时、DNS 失败 | 抓取报 ConnectionError / Timeout | ① Wayback Machine / archive.is ② 搜索引擎摘要先行 ③ RSS/API 绕过 |
| L3（HTTP 应用层） | 403/429/反爬/登录墙 | 抓取报 HTTPError | ① 多源三角确认——转向其他可访问的权威信源 ② 用 `site:`/引号限定 2–3 个搜索词拼合 snippet |

## 平台能力边界清单（哪些能全文抓取、哪些只能到摘要层）

| 平台/站点类型 | 类别 | 全文抓取 | 通道与限制 |
|--------------|------|----------|-----------|
| 知乎（问答/评论） | 民间 | ✅ 全文 | `crawl.py` 走公开 API（L0 层），需 cookie；回答全文 + 赞同数 |
| 政府网站 / 静态门户 | 官方 | ✅ 全文 | `fetch_page.py`，静态 HTML 可完整提取 |
| 国内新闻站（澎湃/新浪/网易等） | 商业媒体 | ✅ 全文 | `fetch_page.py`；部分站点有反爬（403/429）→ 降级 snippet |
| 主流外媒（BBC/Guardian/CNN 等） | 官方外媒 | ❌ 全文 | 被墙，本地无法直连；走 WebSearch snippet（A/B 级）+ GitHub（C 级恢复，见 `foreign-media.md`） |
| 微博 / 豆瓣 / 贴吧 / 公众号 | 民间 SPA | ❌ 全文 | JS 渲染 + 登录墙 + 反爬，`fetch_page.py` 拿不到正文；**只到 WebSearch snippet 层** |
| 论坛（NGA/虎扑/Reddit 等） | 民间 | ⚠️ 视站点 | 静态论坛可抓；需登录/反爬的降级 snippet |

**使用规则**：
- 命中「❌ 全文」的平台 → 直接走 snippet 层或平台 API，**不要对同一 URL 反复重试抓取**
- 命中「⚠️ 视站点」→ 先用 `fetch_page.py` 试一次；提取正文 <50 字符（脚本会提示 SPA/空壳）→ 降级 snippet
- 全文抓取只对「✅」平台成立；民间评论类内容（知乎除外）默认视为只能到摘要层，报告正文引用时标 △

## 知乎工作流

WebSearch 发现 zhihu 链接 → `crawl.py` 单回答/全问题模式获取全文 → 从 md 中提取 `![](url)` → 图片 URL 传入 `collect_images.py` 下载编码 → 文本内容直接作为信源引用。知乎回答在来源索引中标注等级 C（社交平台），正文中引用其事实主张时标注 `△`。

## 核心原则

- 搜索摘要（snippet）为默认信息获取方式，抓取仅在摘要不足时使用。
- Python 脚本单次任务 ≤3 次调用，环境未就绪则回退。
- 禁止用外媒中文版替代英文原站；禁止对同一 URL/被阻断域名反复重试。
- 编码与依赖兜底：`fetch_page.py` 已改为纯 stdlib（无 bs4 依赖）；中文输出一律写 UTF-8 文件后 `read`。
