# -*- coding: utf-8 -*-
"""bai-bao-dai 舆论立场聚类。

读 normalized JSONL，按事件定制的立场词典打标（优先 --stances 文件，
缺省读取 config.json 的 stance_keywords；两者皆空时报错退出）：
- 单标签聚类：一条记录归入命中关键词最多的立场；平票按词典插入顺序取先者；
  无任何命中归入"无关/其他"（避免同一记录同时出现在对立立场下、避免小数条数）。
- 统计各立场占比（按记录数）、高赞代表原文、少数派识别（<10%）、时间轴分布。
- 词典质检：统计各关键词在语料中的文档频率（df），df=0 即零命中——打印警告并写入
  opinion.json 的 dictionary_quality 字段（供修订 stances.json 时剔除幻觉/变体词）。
输出 opinion.json + opinion.md。

用法：
  python opinion.py --in data/demo/normalized/data.jsonl --out data/demo/opinion.json \
      --event "事件关键词" --stances data/demo/stances.json   # 立场词典按事件定制，必需
"""
import argparse
import json
import os
import re
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


_NEG_PREFIX_RE = re.compile(r"[不没无未别莫非](?:是|并|曾|会|能|再)?$")


def _negated_kws(text, hits):
    """返回 hits 中被否定词紧邻前置修饰的关键词（防呆标记）。

    启发式：取关键词每个出现位置前 ≤4 字符，若以"不/没/未/非/别/莫/无…"（可带
    是/并/曾/会/能/再）结尾，视为疑似否定/反讽（如"不支持加装"命中"支持加装"）。
    仅标记供 LLM 复核，不自动改判立场；中文反讽/双重否定无法穷尽，属防呆提示。
    """
    out = []
    for k in hits:
        start = 0
        while True:
            pos = text.find(k, start)
            if pos < 0:
                break
            pre = text[max(0, pos - 4):pos]
            if _NEG_PREFIX_RE.search(pre):
                out.append(k)
                break
            start = pos + len(k)
    return out


def classify(text, stances):
    """单标签聚类。返回 (立场, 命中关键词列表, 疑似否定修饰的关键词列表)。

    命中关键词最多者胜；平票按 stances 插入顺序取先者；无命中归"无关/其他"。
    第三元为 _negated_kws 的防呆标记（疑似反讽/否定，供 LLM 复核，不自动改判）。
    """
    best, best_n, best_kws, best_neg = None, 0, [], []
    for s, kws in stances.items():
        hits = [k for k in kws if k in text]
        neg = _negated_kws(text, hits)
        if len(hits) > best_n:
            best, best_n, best_kws, best_neg = s, len(hits), hits, neg
    if best is None:
        return OTHER, [], []
    return best, best_kws, best_neg


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
    kw_df = Counter()  # 词典质检：各关键词在语料中的文档频率
    timeline = defaultdict(int)
    day_stance = defaultdict(lambda: defaultdict(int))  # 焦点转移：日期 × 立场

    neg_total = 0
    for r in recs:
        text = r.get("content", "")
        label, matched, negated = classify(text, stances)
        neg_total += len(negated)
        likes = (r.get("metrics") or {}).get("likes", 0) or 0
        stance_counter[label] += 1
        stance_likes[label] += likes
        stance_samples[label].append({
            "author": r.get("author", ""), "likes": likes,
            "content": text[:300], "url": r.get("url", ""),
            "matched": matched,
            "negated": negated,  # 疑似否定/反讽防呆标记（供 LLM 复核，不自动改判）
        })
        for kws in stances.values():
            for kw in kws:
                if kw in text:
                    kw_df[kw] += 1
        t = r.get("time", "")
        if t:
            day = str(t)[:10]
            timeline[day] += 1
            day_stance[day][label] += 1

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
        "stance_timeline": {day: dict(day_stance[day]) for day in sorted(day_stance)},
        "note": "单标签聚类：命中关键词最多者胜，平票按词典插入顺序；立场词典按事件定制并经 --stances 传入；少数派阈值 10%；样本 negated 字段 = 命中词被否定词紧邻修饰（疑似反讽/否定，防呆提示，供 LLM 复核，不自动改判）",
    }
    # 词典质检：须遍历词典全部关键词（含零命中者），不能只从 kw_df 派生（零命中词不在其中）
    zero_hit = []
    df_all = {}
    for kw in (k for v in stances.values() for k in v):
        c = kw_df.get(kw, 0)
        df_all[kw] = c
        if c == 0:
            zero_hit.append(kw)
    zero_hit.sort()
    result["dictionary_quality"] = {
        "keyword_total": len(df_all),
        "doc_frequency": dict(sorted(df_all.items(), key=lambda x: -x[1])),
        "zero_hit_keywords": zero_hit,
        "note": "各关键词在语料中的文档频率（df=0 表示零命中，多为幻觉/变体表达，建议剔除或改写后重跑 opinion.py）",
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
            neg = "  [⚠疑似否定]" if s.get("negated") else ""
            lines.append("- [赞%d] %s：%s%s%s" % (
                s["likes"], s["author"], s["content"].replace("\n", " ")[:120], tag, neg))
        lines.append("")
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("[opinion] 共 %d 条，%d 个立场 → %s.json / .md" % (total, len(dist), base))
    if neg_total:
        print("[opinion] 防呆提示：%d 条样本命中词疑似被否定修饰（negated 字段 / opinion.md ⚠ 标记），请 LLM 复核是否为真否定/反讽" % neg_total)
    if zero_hit:
        print("[opinion] 词典质检警告：%d/%d 个关键词语料零命中（df=0），建议剔除或改写后重跑：%s"
              % (len(zero_hit), len(df_all), "、".join(zero_hit)))
    else:
        print("[opinion] 词典质检通过：%d 个关键词在语料中均有命中（逐词 df 见 opinion.json → dictionary_quality）"
              % len(df_all))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
