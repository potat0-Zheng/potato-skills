# -*- coding: utf-8 -*-
"""pan-feng-chao 词表版本管理（E5.2）：改词表＝改口径，必须留痕。

用法：
  python vocab.py --diff 旧.json 新.json                    # 词表差异（新增/删除/改名）
  python vocab.py --diff 旧.json 新.json --corpus 语料.jsonl  # 另给新增词在语料中的文档频率

为什么单独一个脚本：立场占比完全由词表决定，而"改了哪个词"通常只留在人的记忆里。
本工具把词表变更变成可核对的一张表，配合 opinion.py 的 --prev 漂移告警使用：
  词表变了 → vocab.py 告诉你变了什么；数字变了 → opinion.py 告诉你哪一项变了。
零第三方依赖；不改任何词典文件，只读。
"""
import argparse
import io
import json
import os
import sys


def _load(path):
    """读取词表文件：既支持 {立场: [词]}，也支持含 intensity 等附加键的文件。"""
    with io.open(path, encoding="utf-8-sig") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        return {}, {}
    stances = {str(k): [str(x) for x in v if isinstance(x, str) and x.strip()]
               for k, v in obj.items() if isinstance(v, list) and v}
    extra = {k: v for k, v in obj.items() if k not in stances}
    return stances, extra


def _flatten(stances):
    """展平成 {(立场, 词): True}，便于按词比对。"""
    return {"%s\t%s" % (s, w): True for s, ws in (stances or {}).items() for w in ws}


def _bigrams(t):
    return {t[i:i + 2] for i in range(max(0, len(t) - 1))}


def _jaccard(a, b):
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / float(len(ga | gb))


def diff(old_path, new_path, corpus="", rename_threshold=0.5):
    old, old_extra = _load(old_path)
    new, new_extra = _load(new_path)
    fo, fn = _flatten(old), _flatten(new)
    added = [k for k in fn if k not in fo]
    removed = [k for k in fo if k not in fn]
    # 同立场内的"疑似改名"：删除项与新增项字符二元组重合度高
    renamed = []
    used = set()
    for r in removed:
        rs, rw = r.split("\t", 1)
        best, bs = None, 0.0
        for a in added:
            as_, aw = a.split("\t", 1)
            if as_ != rs or a in used:
                continue
            s = _jaccard(rw, aw)
            if s > bs:
                best, bs = a, s
        if best and bs >= float(rename_threshold):
            used.add(best)
            renamed.append({"stance": rs, "old": rw, "new": best.split("\t", 1)[1],
                            "similarity": round(bs, 2)})
    added_left = [a for a in added if a not in used]
    consumed_old = {"%s\t%s" % (x["stance"], x["old"]) for x in renamed}
    removed_left = [r for r in removed if r not in consumed_old]

    df = {}
    if corpus and os.path.exists(corpus):
        rows = []
        with io.open(corpus, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        for a in added_left + ["%s\t%s" % (r["stance"], r["new"]) for r in renamed]:
            w = a.split("\t", 1)[1]
            df[w] = sum(1 for r in rows if w in (r.get("content") or ""))

    return {
        "old": {"path": old_path, "stances": len(old), "keywords": len(fo)},
        "new": {"path": new_path, "stances": len(new), "keywords": len(fn)},
        "added": [{"stance": a.split("\t", 1)[0], "keyword": a.split("\t", 1)[1],
                   "df": df.get(a.split("\t", 1)[1])} for a in added_left],
        "removed": [{"stance": r.split("\t", 1)[0], "keyword": r.split("\t", 1)[1]}
                    for r in removed_left],
        "renamed": renamed,
        "stance_added": sorted(set(new) - set(old)),
        "stance_removed": sorted(set(old) - set(new)),
        "extra_keys_changed": (sorted(set(old_extra) ^ set(new_extra))
                               if old_extra != new_extra else []),
        "note": "改词表会直接改变立场占比与覆盖率：本表用于留痕，任何数字变化都应能在此对上号；"
                "新增词的 df=null 表示未提供 --corpus（要判断它是否在语料里出现，请传语料）。",
    }


def main():
    ap = argparse.ArgumentParser(description="判风潮词表版本差异（只读，不改文件）")
    ap.add_argument("--diff", nargs=2, metavar=("OLD", "NEW"), required=True,
                    help="两份 stances.json：旧 → 新")
    ap.add_argument("--corpus", default="", help="可选：语料 jsonl，用于计算新增词/改名词的文档频率")
    ap.add_argument("--json", action="store_true", help="输出 JSON（默认输出 markdown 表）")
    ap.add_argument("--out", default="", help="结果写入文件（默认打印到 stdout）")
    args = ap.parse_args()

    for p in args.diff:
        if not os.path.exists(p):
            print("[vocab] 文件不存在：%s" % p)
            return 2
    res = diff(args.diff[0], args.diff[1], corpus=args.corpus)

    if args.json:
        text = json.dumps(res, ensure_ascii=False, indent=2)
    else:
        L = ["# 词表差异：%s → %s" % (res["old"]["path"], res["new"]["path"]), "",
             "旧：%d 个立场 / %d 个词　新：%d 个立场 / %d 个词"
             % (res["old"]["stances"], res["old"]["keywords"],
                res["new"]["stances"], res["new"]["keywords"]), ""]
        if res["stance_added"] or res["stance_removed"]:
            L.append("- 立场新增：%s" % ("、".join(res["stance_added"]) or "无"))
            L.append("- 立场移除：%s" % ("、".join(res["stance_removed"]) or "无"))
            L.append("")
        L.append("## 新增关键词（%d）" % len(res["added"]))
        L.append("")
        L.append("| 立场 | 关键词 | 语料 df |")
        L.append("| --- | --- | --- |")
        for x in res["added"]:
            L.append("| %s | %s | %s |" % (x["stance"], x["keyword"],
                                           "—" if x["df"] is None else x["df"]))
        L.append("")
        L.append("## 移除关键词（%d）" % len(res["removed"]))
        L.append("")
        for x in res["removed"]:
            L.append("- %s：%s" % (x["stance"], x["keyword"]))
        L.append("")
        if res["renamed"]:
            L.append("## 疑似改名（%d）" % len(res["renamed"]))
            L.append("")
            for x in res["renamed"]:
                L.append("- %s：%s → %s（相似度 %s）"
                         % (x["stance"], x["old"], x["new"], x["similarity"]))
            L.append("")
        L.append("> %s" % res["note"])
        text = "\n".join(L)

    if args.out:
        with io.open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("[vocab] 差异报告 → %s" % args.out)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
