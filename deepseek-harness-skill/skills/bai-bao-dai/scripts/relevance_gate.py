# -*- coding: utf-8 -*-
"""相关性门（relevance gate）：剔除与事件无关的采集记录。

背景（2026-09 实测教训）：MediaCrawler 关键词搜索可能返回与事件无关的内容
（小红书"最热"排序下尤其严重：检索"菲律宾防长 纸条 中国"曾返回也门军事泄密、
他国法律科普、人身安全提示等爆款笔记）。这些记录进入 normalize 后会：
  1) 污染立场聚类占比；2) 因其高点赞量挤占"高赞样本"展示位。
故在 normalize 之后、opinion 之前插入本门禁。

判定规则（v0.4 起为"父帖定相关、评论随父帖"）：
  · 帖文/回答：正文命中事件特征词 —— 强特征词命中 ≥1 即通过；否则要求弱特征词命中 ≥ --min-hits。
  · 评论：**自身正文不参与判定**（中文评论普遍用「她 / 这孩子」指代，逐条判必然被误杀），
    一律随其父帖（`parent_id`）——父帖相关则评论保留，父帖被剔除则评论一并剔除；
    找不到父帖的评论剔除并单独计数（来源可追溯）。
  · 时间窗不在这里管：由 normalize.py --since/--until 处理（评论按自身时间 + 父帖时间双重把关）。

输出（固定文件名，见 SKILL.md 阶段 2）：
  normalized/data.relevant.jsonl  留下的
  normalized/excluded.jsonl       剔除的（保留可追溯）
  装配端 build_report.py 会自动读取 data.relevant.jsonl 并给出「原始 → 可用」口径。

用法：
  python relevance_gate.py --in data/{event}/normalized/data.jsonl \
      --out data/{event}/normalized/data.relevant.jsonl \
      --excluded data/{event}/normalized/excluded.jsonl \
      --strong "纸条,便条,首尔,防务对话,安全对话,特奥多罗,武官,毛宁,劳改营,古拉格" \
      --weak "菲律宾,菲防长,南海,仲裁,外交部,主办方,韩方,羞辱,羞耻" \
      --min-hits 2
"""
import argparse
import json
import os
import sys
from collections import Counter


def _likes(rec):
    m = rec.get("metrics") or {}
    try:
        return int((m or {}).get("likes") or 0)
    except (TypeError, ValueError):
        return 0


def _split(s):
    return [t.strip() for t in (s or "").split(",") if t.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--excluded", default="")
    ap.add_argument("--strong", default="", help="强特征词（命中 ≥1 即视为相关），逗号分隔")
    ap.add_argument("--weak", default="", help="弱特征词（需命中 --min-hits 个），逗号分隔")
    ap.add_argument("--min-hits", type=int, default=2, help="弱特征词命中阈值（默认 2）")
    ap.add_argument("--json", action="store_true", help="输出结构化摘要")
    args = ap.parse_args()

    strong, weak = _split(args.strong), _split(args.weak)
    if not strong and not weak:
        print("[gate] 错误：至少要提供 --strong 或 --weak 之一，否则无法判定相关性")
        return 2

    # ---- 1) 先判父帖/回答（帖文级）----
    rows = []
    with open(args.src, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    post_ok = {}
    for r in rows:
        if r.get("type") not in ("post", "answer"):
            continue
        if r.get("type") == "answer":
            post_ok[(r.get("platform"), str(r.get("id") or ""))] = True   # 见下：回答恒相关
            continue
        content = r.get("content") or ""
        ok = any(t in content for t in strong) or \
            sum(1 for t in weak if t in content) >= args.min_hits
        post_ok[(r.get("platform"), str(r.get("id") or ""))] = ok

    # ---- 2) 帖文按自身内容判；评论随父帖；回答恒相关 ----
    kept, dropped, reason = [], [], Counter()
    for r in rows:
        if r.get("type") == "answer":
            # 知乎回答不是"关键词搜出来的"，而是直接挂在事件相关问题上（采集端按问题 URL 抓取），
            # 其相关度由问题本身保证；回答又短又口语（"未成年人保护法死哪儿去了"），
            # 用事件特征词判会把有效样本大批误杀——故回答一律保留。
            kept.append(r)
            continue
        if r.get("type") == "post":
            content = r.get("content") or ""
            ok = any(t in content for t in strong) or \
                sum(1 for t in weak if t in content) >= args.min_hits
            if ok:
                kept.append(r)
            else:
                dropped.append(r)
                reason["帖文无事件特征词"] += 1
            continue
        pid = str(r.get("parent_id") or "").strip()
        ok = post_ok.get((r.get("platform"), pid))
        if ok is True:
            kept.append(r)
        elif ok is False:
            dropped.append(r)
            reason["父帖被判无关"] += 1
        else:
            dropped.append(r)
            reason["找不到父帖（无法继承）"] += 1

    for path, rows in ((args.dst, kept), (args.excluded, dropped)):
        if not path:
            continue
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    pct = (100.0 * len(dropped) / (len(kept) + len(dropped))) if (kept or dropped) else 0.0
    likes_all = sum(_likes(r) for r in kept) + sum(_likes(r) for r in dropped)
    likes_bad = sum(_likes(r) for r in dropped)
    lpct = (100.0 * likes_bad / likes_all) if likes_all else 0.0

    per = Counter(r.get("platform") for r in dropped)
    per_type = Counter("%s/%s" % (r.get("platform"), r.get("type")) for r in dropped)
    summary = {
        "total": len(kept) + len(dropped),
        "kept": len(kept),
        "dropped": len(dropped),
        "dropped_pct": round(pct, 1),
        "dropped_likes": likes_bad,
        "dropped_likes_pct": round(lpct, 1),
        "dropped_by_platform": dict(per),
        "dropped_by_type": dict(per_type),
        "dropped_reasons": dict(reason),
        "rule": "帖文按正文特征词；评论随父帖（自身正文不参与判定）",
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print("[gate] 相关 %d 条 / 剔除 %d 条（%.1f%%）｜剔除记录点赞 %d（占全语料点赞 %.1f%%）"
              % (summary["kept"], summary["dropped"], pct, likes_bad, lpct))
        print("[gate] 剔除构成：%s" % dict(per_type))
        print("[gate] 剔除原因：%s" % dict(reason))
        for r in sorted(dropped, key=_likes, reverse=True)[:5]:
            print("       - [%s 赞%s] %s" % (r.get("platform"), _likes(r),
                                             (r.get("content") or "")[:60].replace("\n", " ")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
