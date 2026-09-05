# external 外部爬虫说明

本目录存放 bai-bao-dai 依赖的外部爬虫（微博 / 小红书）。部署状态与使用方法如下。

> **注意**：collect.py 已能自动驱动 MediaCrawler 完成微博/小红书关键词采集（含凭证检测与诚实降级）。
> 以下文档供手动排查与备选方案使用。

## 一、已部署爬虫

| 目录 | 项目 | 用途 | 依赖状态 |
|------|------|------|----------|
| `MediaCrawler/` | NanmiCoder/MediaCrawler (63.7k⭐) | 多平台关键词搜索（微博/小红书/抖音/B站/贴吧/知乎 帖子+评论） | **已就绪**：`.venv`（Python 3.11）+ playwright chromium 已装；教学版已匿名化用户信息 |
| `weiboSpider/` | dataabc/weiboSpider (9.7k⭐) | 按 user_id 爬取指定用户微博全量字段 | 依赖已装；`config.json` 未配置（需 cookie+user_id 才能用） |
| `Spider_XHS/` | cv-cat/Spider_XHS (7.4k⭐) | 小红书关键词搜索笔记+评论 | 依赖已装（含 curl_cffi）；`cookies.txt` 未配置（或每次扫码） |

## 二、凭证状态与获取

| 平台 | 状态 | 获取方式 |
|------|------|----------|
| 知乎 | ✅ 内嵌 cookie 已配置（`../zhihu/cookie.txt`） | 失效时浏览器 F12 重新复制 |
| 微博 | `browser_data/wb_user_data_dir` 有历史登录态（有效性待验证）；也可用 `BBD_WB_COOKIE` 环境变量 | 浏览器登录 weibo.com → F12 复制 Cookie |
| 小红书 | `browser_data/xhs_user_data_dir` 有历史登录态；MediaCrawler `config/base_config.py` 内置 cookie（可能过期）；也可用 `BBD_XHS_COOKIE` | 优先复用登录态；失效则扫码或复制 cookie |

collect.py 凭证检测顺序：`--wb-cookie/--xhs-cookie` 参数 → `BBD_WB_COOKIE/BBD_XHS_COOKIE` 环境变量 → MediaCrawler `browser_data/` 登录态 → 小红书内置 cookie。
**无凭证时 collect.py 明确输出"未采集"并返回非 0，不会静默假成功。**

## 三、手动运行示例（排查用）

### 1. weiboSpider（微博 · 按用户爬取）
- **限制**：需 `user_id` + `cookie`，**不支持关键词搜索**（事件关键词搜索请用 MediaCrawler）
- **配置**：复制 `weiboSpider/weibo_spider/config_sample.json` 为 `config.json`，填入 `cookie` 与 `user_id_list`
- **运行**：
  ```bash
  cd weiboSpider
  python -m weibo_spider --config_path=config.json --output_dir=<输出目录>
  ```

### 2. MediaCrawler（多平台关键词搜索 · 主驱动）
```bash
cd MediaCrawler
.venv\Scripts\python.exe main.py --platform wb --lt cookie --cookies "<cookie>" --type search ^
  --keywords "关键词" --save_data_option jsonl --save_data_path <输出目录> --crawler_max_notes_count 50 --headless y
# 小红书：--platform xhs；复用登录态：--lt qrcode --headless y（browser_data 会话）
```

### 3. Spider_XHS（小红书 · 备选）
```python
from apis.xhs_pc_apis import XHS_Apis
from xhs_utils.xhs_pc import XHSPcAuth
auth = XHSPcAuth.from_qrcode_login(show_in_terminal=True)
api = XHS_Apis(auth).bootstrap()
# api.search_note(...) 关键词搜索；api.get_note_comments(...) 评论
```

## 四、体积说明

- `MediaCrawler/.venv`（约 498 MB）为 Python 虚拟环境（opencv/playwright/pandas 等），可随时删除后用 `uv sync` 或 `venv+pip` 重建；playwright 浏览器二进制位于 `%LOCALAPPDATA%\ms-playwright`，不占用 skill 目录
- `MediaCrawler/browser_data/`（约 43 MB）为扫码登录持久化会话，**建议保留**（可能是现成凭证）

## 五、合规提示

- 三个项目均声明"仅供学习研究，禁止商业用途与大规模爬取"
- 采集仅限公开信息，控制频率（weiboSpider 的 random_wait 已内置限速）
- 事件分析属合理研究范畴，建议匿名化处理（本机 MediaCrawler 教学版已脱敏）
