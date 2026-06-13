"""知乎爬虫 — 单回答 / 全问题 两种模式。"""
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


def api_get(url: str) -> dict:
    resp = session.get(url, headers=HEADERS, timeout=15)
    return resp.json()


# ---- 单回答爬取 ---------------------------------------------
def crawl_single_answer(answer_id: str, out_path: str):
    url = (
        f"https://www.zhihu.com/api/v4/answers/{answer_id}"
        f"?include=content,excerpt,author,question,voteup_count,created_time"
    )
    resp = api_get(url)
    if "error" in resp:
        print(f"API 错误: {resp}")
        return

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
def crawl_whole_question(question_id: str, out_path: str):
    # 标题 & 总数
    probe_url = (
        f"https://www.zhihu.com/api/v4/questions/{question_id}/answers"
        f"?offset=0&limit=1&sort_by=default&platform=desktop"
    )
    probe = api_get(probe_url)
    total = probe["paging"]["totals"]
    title = probe["data"][0]["question"]["title"]
    print(f"问题: {title}  |  总回答数: {total}")

    all_data = []
    limit = 20
    for offset in range(0, total, limit):
        page_url = (
            f"https://www.zhihu.com/api/v4/questions/{question_id}/answers"
            f"?include=data%5B*%5D.content,excerpt,author,voteup_count"
            f"&offset={offset}&limit={limit}&sort_by=default&platform=desktop"
        )
        page = api_get(page_url)
        if "data" not in page:
            print(f"  offset={offset} 异常: {page}")
            break
        all_data.extend(page["data"])
        pct = min(100, round((offset + limit) / total * 100, 1))
        print(f"\r  进度: {pct}% ({len(all_data)}/{total})", end="", flush=True)
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
    if len(sys.argv) < 3:
        print("用法: python crawl.py <知乎URL> <输出文件路径>")
        print("示例: python crawl.py https://www.zhihu.com/question/27934143/answer/xxx result.md")
        sys.exit(1)

    raw_url = sys.argv[1]
    out = sys.argv[2]

    parsed = parse_url(raw_url)

    if not parsed["question_id"]:
        print("无法从 URL 中提取问题 ID，请检查链接格式。")
        sys.exit(1)

    if not COOKIE:
        print("未找到 cookie，请将知乎 cookie 写入 " + COOKIE_FILE)

    if parsed["answer_id"]:
        print(f"模式: 单回答  |  answer_id={parsed['answer_id']}")
        crawl_single_answer(parsed["answer_id"], out)
    else:
        print(f"模式: 全问题  |  question_id={parsed['question_id']}")
        crawl_whole_question(parsed["question_id"], out)
