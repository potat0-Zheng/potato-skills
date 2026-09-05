# -*- coding: utf-8 -*-
"""bai-bao-dai 舆论立场聚类。

读 normalized JSONL，按事件定制的立场词典打标（优先 --stances 文件，
缺省读取 config.json 的 stance_keywords；两者皆空时报错退出）：
- 单标签聚类：一条记录归入命中关键词最多的立场；平票按词典插入顺序取先者；
  无任何命中归入"无关/其他"（避免同一记录同时出现在对立立场下、避免小数条数）。
- 统计各立场占比（按记录数）、高赞代表原文、少数派识别（<10%）、时间轴分布。
输出 opinion.json + opinion.md。

用法：
  python opinion.py --in data/demo/normalized/data.jsonl --out data/demo/opinion.json \
      --event "事件关键词" --stances data/demo/stances.json   # 立场词典按事件定制，必需
"""
import argparse
import json
import os
from collections import Counter, defaultdict

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 立场词典按事件定制（--stances），不设事件性兜底默认；
# 词典为空时 main() 报错退出，防止静默错误归类。
FALLBACK_STANCE = {}
OTHER = "无关/其他"


def _clean_stances(st):
    """仅保留"值为非空字符串列表"的项（防注释等非列表值被迭代）。"""
    out = {}
    for k, v in (st or {}).items():
        if not isinstance(v, list):
            continue
        kws = [x for x in v if isinstance(x, str) and x.strip()]
        if kws:
            out[str(k)] = kws
    return out


def load_stances(path=""):
    """加载立场词典：优先 --stances 指定的文件；否则用 config.json 的 stance_keywords。

    词典须按事件定制；两者皆空时返回 {}（由 main() 报错退出）。
    """
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                return _clean_stances(json.load(f))
        except Exception as e:
            print("[opinion] 警告：--stances 读取失败（%s），回退 config.json" % e)
    p = os.path.join(SKILL_DIR, "config.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return _clean_stances(json.load(f).get("stance_keywords", {}))
    except Exception:
        return {}


def classify(text, stances):
    """单标签聚类。返回 (立场, 命中关键词列表)。

    命中关键词最多者胜；平票按 stances 插入顺序取先者；无命中归"无关/其他"。
    """
    best, best_n, best_kws = None, 0, []
    for s, kws in stances.items():
        hits = [k for k in kws if k in text]
        if len(hits) > best_n:
            best, best_n, best_kws = s, len(hits), hits
    if best is None:
        return OTHER, []
    return best, best_kws


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--event", default="")
    ap.add_argument("--stances", default="", help="事件定制立场词典 json（{\"立场\": [\"关键词\",...]}），缺省用 config.json")
    args = ap.parse_args()

    stances = load_stances(args.stances)
    if not stances:
        print("[opinion] 错误：未提供立场词典（--stances 缺失或 config.json stance_keywords 为空）。")
        print("[opinion] 立场词典须按事件定制：依据采集内容与首轮搜索的观点分歧构造 {立场: [关键词,...]}，")
        print('[opinion] 例如：opinion.py --in <normalized.jsonl> --out <opinion.json> --event "事件" --stances data/<event_id>/stances.json')
        return 1
    recs = []
    with open(args.src, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                recs.append(json.loads(line))

    stance_counter = Counter()
    stance_likes = Counter()
    stance_samples = defaultdict(list)
    timeline = defaultdict(int)

    for r in recs:
        text = r.get("content", "")
        label, matched = classify(text, stances)
        likes = (r.get("metrics") or {}).get("likes", 0) or 0
        stance_counter[label] += 1
        stance_likes[label] += likes
        stance_samples[label].append({
            "author": r.get("author", ""), "likes": likes,
            "content": text[:300], "url": r.get("url", ""),
            "matched": matched,
        })
        t = r.get("time", "")
        if t:
            day = str(t)[:10]
            timeline[day] += 1

    total = len(recs)
    dist = []
    for s, cnt in stance_counter.most_common():
        pct = round(cnt / total * 100, 1) if total else 0
        samples = sorted(stance_samples[s], key=lambda x: -x["likes"])[:3]
        dist.append({
            "stance": s,
            "count": cnt,
            "pct": pct,
            "likes": int(stance_likes[s]),
            "minority": total > 0 and pct < 10,  # 少数派标记
            "top_samples": samples,
        })

    result = {
        "event": args.event,
        "total_records": total,
        "stance_distribution": dist,
        "timeline": dict(sorted(timeline.items())),
        "note": "单标签聚类：命中关键词最多者胜，平票按词典插入顺序；立场词典按事件定制并经 --stances 传入；少数派阈值 10%",
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    base = os.path.splitext(args.out)[0]
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 生成 opinion.md
    lines = ["# 舆论立场聚类：%s" % args.event, "", "> 共 %d 条记录" % total, ""]
    for d in dist:
        flag = " [少数派]" if d["minority"] else ""
        lines.append("## %s（%.1f%% · %d 条）%s" % (d["stance"], d["pct"], d["count"], flag))
        for s in d["top_samples"]:
            tag = "  [命中: %s]" % "/".join(s["matched"]) if s["matched"] else ""
            lines.append("- [赞%d] %s：%s%s" % (
                s["likes"], s["author"], s["content"].replace("\n", " ")[:120], tag))
        lines.append("")
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("[opinion] 共 %d 条，%d 个立场 → %s.json / .md" % (total, len(dist), base))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
