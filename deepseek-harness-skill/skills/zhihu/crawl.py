"""知乎爬虫 — 单回答 / 全问题 两种模式。

v2 改进：
- 无 cookie 时优雅退出并给出指引（不再裸 KeyError 崩溃）
- API 请求统一做 HTTP 状态 / JSON / error 检查，失败时明确提示 cookie 缺失或过期
- 全问题模式新增可选 --limit N（限制爬取条数，便于轻量浏览与测试）
"""
import re
import sys
import os
import json
import time
import requests

# ---- 配置 --------------------------------------------------
COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookie.txt")


def load_cookie():
    if os.path.exists(COOKIE_FILE):
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""


COOKIE = load_cookie()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Cookie": COOKIE,
    "Referer": "https://www.zhihu.com/",
}

session = requests.Session()
session.trust_env = False


def check_cookie():
    """无 cookie 时直接退出并给出获取指引。"""
    if not COOKIE:
        print("未找到 cookie，请将知乎 cookie 写入 " + COOKIE_FILE)
        print("获取方法：浏览器登录知乎 → F12 → Network → 刷新 → 复制任一 api/v4 请求的完整 Cookie 值")
        sys.exit(2)


# ---- URL 解析 ------------------------------------------------
def parse_url(url: str) -> dict:
    """从知乎 URL 提取 question_id 和可选的 answer_id。"""
    qm = re.search(r"/question/(\d+)", url)
    am = re.search(r"/answer/(\d+)", url)
    return {
        "question_id": qm.group(1) if qm else None,
        "answer_id": am.group(1) if am else None,
    }


# ---- HTML → Markdown ----------------------------------------
def html_to_md(text: str) -> str:
    text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = text.replace("<p>", "\n").replace("</p>", "\n")
    text = text.replace("<b>", "**").replace("</b>", "**")
    text = text.replace("<strong>", "**").replace("</strong>", "**")
    text = text.replace("<i>", "*").replace("</i>", "*")
    text = text.replace("<em>", "*").replace("</em>", "*")
    text = re.sub(r'<a.*?href="(.*?)".*?>(.*?)</a>', r"[\2](\1)", text)
    text = re.sub(r'<img.*?src="(.*?)".*?>', r"![](\1)", text)
    text = re.sub(r"<h[1-6]>(.*?)</h[1-6]>", r"\n## \1\n", text)
    text = re.sub(r"<li>(.*?)</li>", r"- \1", text)
    text = re.sub(r"<blockquote.*?>(.*?)</blockquote>", r"> \1", text, flags=re.DOTALL)
    text = re.sub(r"<figure>(.*?)</figure>", "", text, flags=re.DOTALL)
    text = re.sub(r"<figcaption>(.*?)</figcaption>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    for entity, char in [("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"),
                         ("&amp;", "&"), ("&quot;", '"'),
                         ("&#34;", '"'), ("&#39;", "'")]:
        text = text.replace(entity, char)
    text = re.sub(r"&#\d+;", "", text)
    return text.strip()


def api_get(url: str):
    """带检查的 GET。失败返回 None（调用方据此优雅退出）。"""
    try:
        resp = session.get(url, headers=HEADERS, timeout=15)
    except requests.RequestException as e:
        print(f"[错误] 网络请求失败: {e}")
        return None
    if resp.status_code != 200:
        print(f"[错误] HTTP {resp.status_code} —— cookie 缺失/过期或请求被拒，请更新 {COOKIE_FILE}")
        return None
    try:
        return resp.json()
    except ValueError:
        print("[错误] 响应不是 JSON（可能触发风控或需要登录验证）")
        return None


# ---- 单回答爬取 ---------------------------------------------
def crawl_single_answer(answer_id: str, out_path: str):
    url = (
        f"https://www.zhihu.com/api/v4/answers/{answer_id}"
        f"?include=content,excerpt,author,question,voteup_count,created_time"
    )
    resp = api_get(url)
    if resp is None:
        sys.exit(3)
    if "error" in resp:
        print(f"API 错误: {resp}")
        sys.exit(3)

    question = resp.get("question", {})
    author = resp.get("author", {})
    content = resp.get("content", "")
    title = question.get("title", "未知问题")
    qid = question.get("id", "")
    name = author.get("name", "匿名用户")
    url_token = author.get("url_token", "")
    voteup = resp.get("voteup_count", 0)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write(f"> 问题ID：{qid} | 回答ID：{answer_id}\n\n")
        f.write(f"> 来源：https://www.zhihu.com/question/{qid}/answer/{answer_id}\n\n")
        f.write("---\n\n")
        f.write(f"## {name}\n\n")
        f.write(f"- 赞同数：{voteup}\n")
        if url_token:
            f.write(f"- 主页：https://www.zhihu.com/people/{url_token}\n")
        f.write(f"\n{html_to_md(content)}\n")

    print(f"标题: {title}")
    print(f"作者: {name} | 赞同: {voteup}")
    print(f"已写入 {out_path}")


# ---- 全问题爬取 ---------------------------------------------
def crawl_whole_question(question_id: str, out_path: str, limit=None):
    # 标题 & 总数（探测）
    probe_url = (
        f"https://www.zhihu.com/api/v4/questions/{question_id}/answers"
        f"?offset=0&limit=1&sort_by=default&platform=desktop"
    )
    probe = api_get(probe_url)
    if probe is None:
        sys.exit(3)
    if "error" in probe or "paging" not in probe:
        print(f"[错误] 无法获取问题信息（cookie 缺失/过期，或问题不存在）: {probe.get('error', probe)}")
        sys.exit(3)

    total = probe["paging"]["totals"]
    title = probe["data"][0]["question"]["title"]
    print(f"问题: {title}  |  总回答数: {total}")

    if limit is not None and limit < total:
        total = limit
        print(f"（--limit 生效，仅爬取前 {limit} 条）")

    all_data = []
    page_size = 20
    for offset in range(0, total, page_size):
        page_url = (
            f"https://www.zhihu.com/api/v4/questions/{question_id}/answers"
            f"?include=data%5B*%5D.content,excerpt,author,voteup_count"
            f"&offset={offset}&limit={page_size}&sort_by=default&platform=desktop"
        )
        page = api_get(page_url)
        if page is None:
            break
        if "data" not in page:
            print(f"  offset={offset} 异常: {page}")
            break
        data = page["data"]
        # 截断到目标条数（--limit 生效时，避免一页 20 条整页超出）
        remain = total - len(all_data)
        if len(data) > remain:
            data = data[:remain]
        all_data.extend(data)
        pct = min(100, round(len(all_data) / total * 100, 1))
        print(f"\r  进度: {pct}% ({len(all_data)}/{total})", end="", flush=True)
        if len(all_data) >= total:
            break
        time.sleep(0.5)
    print()

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write(f"> 问题ID：{question_id} | 总回答数：{total} | 实际爬取：{len(all_data)}\n\n")
        f.write(f"> 来源：https://www.zhihu.com/question/{question_id}\n\n")
        f.write("---\n\n")
        for i, item in enumerate(all_data):
            author = item.get("author", {})
            name = author.get("name", "匿名用户")
            aid = author.get("id", "")
            url_token = author.get("url_token", "")
            content = item.get("content", "")
            voteup = item.get("voteup_count", 0)

            f.write(f"## [{i+1}] {name}\n\n")
            f.write(f"- 回答者ID：{aid}\n")
            f.write(f"- 赞同数：{voteup}\n")
            if url_token:
                f.write(f"- 主页：https://www.zhihu.com/people/{url_token}\n")
            f.write(f"\n{html_to_md(content)}\n\n")
            f.write("---\n\n")

    print(f"完成，共 {len(all_data)} 条回答 → {out_path}")


# ---- 入口 ---------------------------------------------------
if __name__ == "__main__":
    # 解析可选 --limit N（放任意位置）
    limit = None
    args = []
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1]); i += 2
        else:
            args.append(sys.argv[i]); i += 1

    if len(args) < 2:
        print("用法: python crawl.py <知乎URL> <输出文件路径> [--limit N]")
        print("示例: python crawl.py https://www.zhihu.com/question/27934143/answer/xxx result.md")
        print("      python crawl.py https://www.zhihu.com/question/27934143 result.md --limit 5")
        sys.exit(1)

    raw_url, out = args[0], args[1]
    parsed = parse_url(raw_url)

    if not parsed["question_id"]:
        print("无法从 URL 中提取问题 ID，请检查链接格式。")
        sys.exit(1)

    check_cookie()

    if parsed["answer_id"]:
        print(f"模式: 单回答  |  answer_id={parsed['answer_id']}")
        crawl_single_answer(parsed["answer_id"], out)
    else:
        print(f"模式: 全问题  |  question_id={parsed['question_id']}")
        crawl_whole_question(parsed["question_id"], out, limit)
