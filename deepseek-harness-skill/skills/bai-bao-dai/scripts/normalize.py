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


def _is_answer_block(blk):
    """判断一个 `## ` 块是"真回答"还是"回答正文里的二级标题"（v0.4 修）。

    爬虫输出的真回答块必带元信息行（回答者ID/赞同数/回答链接/主页）；回答正文里自带的
    `## 小标题`（或被人为转成 `## ` 的加粗标题）没有这些行——旧实现把它们当新回答，
    于是一篇回答被切成多条记录，作者/id 都是小标题文本，凭空抬高条数占比。
    """
    return bool(re.search(r"^-\s+(回答者ID|赞同数|回答链接|主页)：", blk, re.M))


def parse_zhihu_md(text, event, source_url):
    """解析知乎 Markdown，返回记录列表。"""
    records = []
    dropped_frag = 0
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
        if not _is_answer_block(blk):
            # 不是回答（是回答正文里的小标题/续写段）：并入上一条，不新增记录
            frag = re.sub(r"^##\s+.*$", "", blk, count=1, flags=re.M).strip()
            if frag:
                if records:
                    records[-1]["content"] = (records[-1]["content"] + "\n\n" + frag).strip()
                else:
                    dropped_frag += 1
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
            "parent_id": "",
            "event_keyword": event,
        })
    if dropped_frag:
        print("[normalize] 警告：%d 个游离小节（无前置回答）被丢弃" % dropped_frag)
    return records


# ---------- 时间窗与评论归时（v0.4）----------
def _day(v):
    s = str(v or "")[:10]
    return s if re.match(r"^\d{4}-\d{2}-\d{2}$", s) else ""


def apply_time_window(recs, since="", until=""):
    """按时间窗过滤，并给评论补"自身时间"。

    规则（CONTRACT-joint §10.2）：
      1. 帖文/回答：时间缺失则保留并标 time_missing；时间越窗则剔除。
      2. 评论：自身时间优先；缺失时继承父帖时间（标 time_inherited）。父帖时间越窗 → 评论一并剔除——
         评论不能只凭"父帖相关"就留在分析语料里。
    返回 (kept, stats)；stats 含各剔除原因计数，供报告如实披露。
    """
    since, until = _day(since), _day(until)
    post_day = {}
    for r in recs:
        if r.get("type") in ("post", "answer"):
            post_day[(r.get("platform"), str(r.get("id") or ""))] = _day(r.get("time"))
    kept, stats = [], {"in_window": 0, "before": 0, "after": 0, "parent_out": 0,
                       "time_missing": 0, "time_inherited": 0}
    out_window = []
    for r in recs:
        t = _day(r.get("time"))
        if not t and r.get("type") == "comment":
            pt = post_day.get((r.get("platform"), str(r.get("parent_id") or "")))
            if pt:
                t = pt
                r["time"] = pt
                r["time_inherited"] = True
                stats["time_inherited"] += 1
        r["_day"] = t
        if not t:
            stats["time_missing"] += 1
            kept.append(r)
            continue
        if since and t < since:
            stats["before"] += 1
            r["drop_reason"] = "早于事件起点"
            out_window.append(r)
            continue
        if until and t > until:
            stats["after"] += 1
            r["drop_reason"] = "晚于信息截止"
            out_window.append(r)
            continue
        if r.get("type") == "comment":
            pt = post_day.get((r.get("platform"), str(r.get("parent_id") or "")))
            if pt and ((since and pt < since) or (until and pt > until)):
                stats["parent_out"] += 1
                r["drop_reason"] = "父帖越出时间窗"
                out_window.append(r)
                continue
        stats["in_window"] += 1
        kept.append(r)
    return kept, stats, out_window


def _platform_from_path(path):
    """按来源文件路径判定平台（v0.3）。

    原实现以帖子字段（shared_count/comments_count/...）判平台；评论记录只有 comment_id
    与 *_like_count，因而恒被判为 "unknown"（本机实测 896/1170 条）。
    """
    p = str(path or "").replace("\\", "/").lower()
    if "/xiaohongshu/" in p:
        return "xiaohongshu"
    if "/weibo/" in p:
        return "weibo"
    if "/zhihu/" in p:
        return "zhihu"
    return ""


def map_mediacrawler(d, fallback_platform=""):
    """MediaCrawler jsonl 记录 → 统一 schema（微博/小红书 帖子与评论）。

    fallback_platform：记录缺帖子字段时按来源路径回填的平台（v0.3，修 unknown 缺陷）。

    MediaCrawler（教学版）字段：
      微博帖: note_id/content/create_time(epoch)/create_date_time/liked_count/comments_count/shared_count/note_url/nickname
      微博评: comment_id/note_id/content/create_date_time/comment_like_count/nickname
      小红书帖: note_id/type/title/desc/time(epoch)/liked_count/comment_count/share_count/note_url/nickname
      小红书评: comment_id/note_id/content/create_time(epoch)/like_count/nickname
    """
    platform = "weibo" if "shared_count" in d or "comments_count" in d else \
               "xiaohongshu" if "collected_count" in d or "share_count" in d else \
               d.get("platform", "")
    if platform in ("", "unknown"):
        platform = fallback_platform or "unknown"
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
        "parent_id": str(d.get("note_id") or "") if "comment_id" in d else "",
        "event_keyword": d.get("source_keyword", ""),
    }


def pass_through_jsonl(path):
    """非知乎来源归一化：识别 MediaCrawler schema 映射，其余透传。"""
    fallback = _platform_from_path(path)
    recs = []
    with open(path, "r", encoding="utf-8-sig") as f:
        raw = f.read().strip()
    if raw.startswith("["):
        data = json.loads(raw)
    else:
        data = [json.loads(l) for l in raw.splitlines() if l.strip()]
    for d in data:
        if "note_id" in d or "comment_id" in d:
            recs.append(map_mediacrawler(d, fallback))
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
            "parent_id": str(d.get("parent_id") or ""),
            "event_keyword": d.get("event_keyword", ""),
        })
    return recs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True, help="输入：目录或单个 .md/.jsonl 文件")
    ap.add_argument("--out", required=True, help="输出 JSONL 路径")
    ap.add_argument("--event", default="", help="事件关键词")
    ap.add_argument("--since", default="", help="事件起始日 YYYY-MM-DD：早于该日的记录剔除（含评论）")
    ap.add_argument("--until", default="", help="信息截止日 YYYY-MM-DD：晚于该日的记录剔除")
    ap.add_argument("--keep-missing-time", action="store_true",
                    help="保留时间缺失的记录（默认保留但计数披露；本开关仅为显式声明意图）")
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

    n_raw = len(recs)
    stats, out_window = None, []
    if args.since or args.until:
        recs, stats, out_window = apply_time_window(recs, args.since, args.until)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for r in recs:
            r.pop("_day", None)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    # 越窗记录另存一档（不是删除）：留作背景/语境人工复核，不进分析语料
    if out_window:
        pw = os.path.join(os.path.dirname(os.path.abspath(args.out)), "data.prewindow.jsonl")
        with open(pw, "w", encoding="utf-8") as f:
            for r in out_window:
                r.pop("_day", None)
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print("[normalize] 越窗 %d 条另存 → %s（背景语境，勿并入分析语料）" % (len(out_window), pw))
    print(f"[normalize] 共归一化 {len(recs)} 条 → {args.out}")
    if stats:
        print("[normalize] 时间窗 %s ~ %s：保留 %d / 原始 %d（剔除 早于起点 %d · 晚于截止 %d · "
              "父帖越窗 %d；补时 %d 条继承父帖时间；无时间 %d 条保留待人工核）"
              % (args.since or "—", args.until or "—", len(recs), n_raw, stats["before"],
                 stats["after"], stats["parent_out"], stats["time_inherited"], stats["time_missing"]))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
