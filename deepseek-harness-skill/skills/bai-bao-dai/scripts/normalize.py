# -*- coding: utf-8 -*-
"""bai-bao-dai 多源归一化。

将各平台采集的原始输出归一化为统一 JSONL（每行一条记录）：
  {"platform","type","id","author","time","content","metrics","url","event_keyword"}

当前完整支持知乎（解析 crawl.py 输出的 Markdown，单回答/全问题两种格式）；
微博/小红书为预留（接受 JSONL/CSV，字段名对齐后透传）。

用法：
  python normalize.py --in data/demo/raw/zhihu --out data/demo/normalized --event "关键词"
  python normalize.py --in 某知乎.md --out out.jsonl --event "关键词"   # 单文件
"""
import argparse
import glob
import json
import os
import re
from datetime import datetime


def _fmt_epoch(ts):
    """epoch 秒 → 'YYYY-MM-DD HH:MM'；支持毫秒（>1e12 自动折算）；非数值/非法返回原样。"""
    if isinstance(ts, (int, float)) or (isinstance(ts, str) and re.fullmatch(r"\d+(\.\d+)?", ts)):
        try:
            v = float(ts)
            if v > 1e12:      # 毫秒时间戳
                v /= 1000.0
            if v <= 0:
                return ""
            return datetime.fromtimestamp(v).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError, OverflowError):
            return str(ts)
    return str(ts or "")


_CN_NUM = {"万": 1e4, "亿": 1e8}


def _parse_cn_count(s):
    """解析点赞/评论等计数：支持 '3.6万'、'1.2亿'、'1234'、空串。返回 int。"""
    if s is None:
        return 0
    s = str(s).strip().replace(",", "")
    if not s:
        return 0
    m = re.match(r"^([\d.]+)\s*(万|亿)?$", s)
    if not m:
        try:
            return int(float(s))
        except ValueError:
            return 0
    num = float(m.group(1))
    unit = m.group(2)
    return int(num * _CN_NUM[unit]) if unit else int(num)


def parse_zhihu_md(text, event, source_url):
    """解析知乎 Markdown，返回记录列表。"""
    records = []
    # 标题
    title_m = re.search(r"^#\s+(.+)$", text, re.M)
    title = title_m.group(1).strip() if title_m else ""
    # 来源 URL
    src_m = re.search(r"来源：(\S+)", text)
    url = src_m.group(1).strip() if src_m else source_url

    # 切分每个回答块：## [N] 作者 或 ## 作者
    # 全问题：[i] 前缀；单回答：无序号
    blocks = re.split(r"\n(?=## )", text)
    for blk in blocks:
        head = re.match(r"^##\s+\[?\d*\]?\s*(.+)", blk)
        if not head:
            continue
        author = head.group(1).strip()
        vote = re.search(r"赞同数：(\d+)", blk)
        aid = re.search(r"回答者ID：(\S+)", blk)
        homepage = re.search(r"主页：(\S+)", blk)
        answer_url = re.search(r"回答链接：(\S+)", blk)
        created = re.search(r"发布时间：([0-9]{4}-[0-9]{2}-[0-9]{2}(?: [0-9]{2}:[0-9]{2})?)", blk)
        # 正文：去掉头部元信息行后的剩余内容
        content = re.sub(r"^##\s+.*$", "", blk, count=1, flags=re.M)
        content = re.sub(r"^-\s+(回答者ID|赞同数|主页|回答链接|发布时间)：.*$", "", content, flags=re.M)
        content = re.sub(r"^---+\s*$", "", content, flags=re.M)
        content = content.strip()
        if not content:
            continue
        records.append({
            "platform": "zhihu",
            "type": "answer",
            "id": aid.group(1) if aid else author,
            "author": author,
            "time": created.group(1) if created else "",
            "content": content,
            "metrics": {"likes": int(vote.group(1)) if vote else 0,
                        "comments": 0, "reposts": 0},
            "url": (answer_url.group(1) if answer_url else homepage.group(1) if homepage else url),
            "event_keyword": event,
        })
    return records


def map_mediacrawler(d):
    """MediaCrawler jsonl 记录 → 统一 schema（微博/小红书 帖子与评论）。

    MediaCrawler（教学版）字段：
      微博帖: note_id/content/create_time(epoch)/create_date_time/liked_count/comments_count/shared_count/note_url/nickname
      微博评: comment_id/note_id/content/create_date_time/comment_like_count/nickname
      小红书帖: note_id/type/title/desc/time(epoch)/liked_count/comment_count/share_count/note_url/nickname
      小红书评: comment_id/note_id/content/create_time(epoch)/like_count/nickname
    """
    platform = "weibo" if "shared_count" in d or "comments_count" in d else \
               "xiaohongshu" if "collected_count" in d or "share_count" in d else \
               d.get("platform", "unknown")
    if "comment_id" in d:
        rtype, rid = "comment", d.get("comment_id")
        content = d.get("content", "")
        likes = _parse_cn_count(d.get("comment_like_count") or d.get("like_count"))
    else:
        rtype, rid = "post", d.get("note_id")
        raw_content = d.get("content") or ""   # 微博用 content
        desc = d.get("desc", "") or ""          # 小红书用 desc/title
        title = d.get("title", "") or ""
        if raw_content:
            content = raw_content
        else:
            content = (title + "\n" + desc).strip() if title and title != desc else (desc or title or "")
        likes = _parse_cn_count(d.get("liked_count"))
    time_v = d.get("create_date_time") or d.get("create_time") or d.get("time") or ""
    return {
        "platform": platform,
        "type": rtype,
        "id": str(rid or ""),
        "author": d.get("nickname", ""),
        "time": str(time_v) if str(time_v).count("-") >= 2 else _fmt_epoch(time_v),
        "content": content,
        "metrics": {
            "likes": likes,
            "comments": _parse_cn_count(d.get("comments_count") or d.get("comment_count")),
            "reposts": _parse_cn_count(d.get("shared_count") or d.get("share_count")),
        },
        "url": d.get("note_url", ""),
        "event_keyword": d.get("source_keyword", ""),
    }


def pass_through_jsonl(path):
    """非知乎来源归一化：识别 MediaCrawler schema 映射，其余透传。"""
    recs = []
    with open(path, "r", encoding="utf-8-sig") as f:
        raw = f.read().strip()
    if raw.startswith("["):
        data = json.loads(raw)
    else:
        data = [json.loads(l) for l in raw.splitlines() if l.strip()]
    for d in data:
        if "note_id" in d or "comment_id" in d:
            recs.append(map_mediacrawler(d))
            continue
        # 通用透传
        recs.append({
            "platform": d.get("platform", "unknown"),
            "type": d.get("type", "post"),
            "id": str(d.get("id", "")),
            "author": d.get("author", d.get("user", "")),
            "time": d.get("time", ""),
            "content": d.get("content", ""),
            "metrics": d.get("metrics", {"likes": d.get("likes", 0),
                                         "comments": d.get("comments", 0),
                                         "reposts": d.get("reposts", 0)}),
            "url": d.get("url", ""),
            "event_keyword": d.get("event_keyword", ""),
        })
    return recs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True, help="输入：目录或单个 .md/.jsonl 文件")
    ap.add_argument("--out", required=True, help="输出 JSONL 路径")
    ap.add_argument("--event", default="", help="事件关键词")
    args = ap.parse_args()

    recs = []
    if os.path.isdir(args.src):
        files = sorted(glob.glob(os.path.join(args.src, "**", "*"), recursive=True))
        for fp in files:
            if fp.endswith(".md"):
                with open(fp, "r", encoding="utf-8-sig") as f:
                    recs += parse_zhihu_md(f.read(), args.event, "")
            elif fp.endswith(".jsonl") or fp.endswith(".json"):
                recs += pass_through_jsonl(fp)
    elif args.src.endswith(".md"):
        with open(args.src, "r", encoding="utf-8-sig") as f:
            recs = parse_zhihu_md(f.read(), args.event, args.src)
    elif args.src.endswith(".jsonl") or args.src.endswith(".json"):
        recs = pass_through_jsonl(args.src)
    else:
        print("[错误] 不支持的输入类型（需 .md / .jsonl / .json 或目录）")
        return 1

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[normalize] 共归一化 {len(recs)} 条 → {args.out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
