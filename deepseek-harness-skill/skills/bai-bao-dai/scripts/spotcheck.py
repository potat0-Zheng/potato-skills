# -*- coding: utf-8 -*-
"""bai-bao-dai 人工抽检（v0.4）：把"必须抽检"从口号变成工件。

背景：四个技能都要求"分类结果必须抽检才能引用"，但此前没有工件承接——
没有输入格式、没有落盘位置、报告里也没位置显示，于是抽检永远被跳过，
报告里的占比始终是一个裸数字。

本脚本做三件事：
  1. `--make`  分层配额抽样，生成判读单（spotcheck.json 待填 + spotcheck.md 人读）
  2. `--apply` 读回判读结果，计算误判率 / 加权回推，并把结论写回 opinion.json
  3. `--report`（可选）把结论渲染成报告第五章节用的 markdown

三种核（`--kind`）：
  stance       立场标签核：每个立场抽 N 条，判"这条真的属于该立场吗"（对/错）
  rule         规则层核：只抽规则层命中项，同上（正则只能命中"像"某立场的表述，误判率高是预期）
  unclassified 未分类段核：判"这条含立场表达吗"，含则选立场；按分层配额加权回推到全段

分层维度 = 平台 × 点赞档（× 立场，仅 stance 核）。固定 --seed 保证任何人可复现同一批样本。

判读纪律（脚本强制）：
  · 逐条必须填 verdict / has_stance，缺任何一条 → --apply 失败，不产出"部分结果"
  · 未抽检（status != 已完成）时，报告只能把占比读作"未校正"；门禁 G11 会拦

用法：
  python spotcheck.py --kind stance --in data/{event}/normalized/data.relevant.jsonl \
      --opinion data/{event}/opinion.json --out data/{event}/spotcheck.json --make --n 10
  # —— 人工在 spotcheck.md 上逐条判读，把结论填回 spotcheck.json ——
  python spotcheck.py --kind stance --in ... --opinion ... --out ... --apply
"""
import argparse
import collections
import io
import json
import os
import random
import re
import sys

# 与 opinion.py 保持一致的读法（不 import，避免脚本互依赖）
OTHER_NAMES = {"未识别", "无关/其他"}


def _likes(r):
    return int(((r.get("metrics") or {}).get("likes", 0) or 0))


def _band(r):
    lk = _likes(r)
    return "高赞(>=100)" if lk >= 100 else ("中赞(10-99)" if lk >= 10 else "低赞(<10)")


def _load_jsonl(p):
    rows = []
    with io.open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_json(p):
    if not p or not os.path.exists(p):
        return None
    with io.open(p, encoding="utf-8-sig") as f:
        return json.load(f)


def _save_json(p, obj):
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _stance_of(r, opinion, id2label=None):
    """从 opinion.json 反查某条记录的立场标签（按 platform+id 匹配 top_samples 覆盖不到全量，
    故优先用调用方传入的 id2label 映射）。"""
    if id2label is not None:
        return id2label.get((r.get("platform"), str(r.get("id") or "")), "")
    return ""


def build_id2label(opinion_path, src_path, stances, rules_path=""):
    """用 opinion.py 的同一套判据重算全量标签，避免"抽样样本与聚类结果不一致"。

    返回 (id2label, stances_cfg, rules)；id2label 的键是 (platform, id)，
    与语料记录一一对应（同一条记录只会有一个签名，故不会串号）。
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import opinion as op
    stances_cfg = _load_json(stances) or {}
    rules, _ = op.load_rules(rules_path) if rules_path else ({}, [])
    sig = {}
    for r in _load_jsonl(src_path):
        k = (r.get("platform"), str(r.get("id") or ""))   # 唯一签名只判一次（同帖多评不重复判）
        if k in sig:
            continue
        s, _, _ = op.classify(r.get("content", "") or "", stances_cfg)
        sig[k] = s
    return sig, stances_cfg, rules


def sample_strata(rows, key_fn, n_total, rng):
    """按层配额抽样：每层配额 ∝ 层大小，至少 1 条（层数 > n_total 时按层大小优先）。"""
    strata = collections.defaultdict(list)
    for r in rows:
        strata[key_fn(r)].append(r)
    keys = sorted(strata, key=lambda k: -len(strata[k]))
    if len(keys) > n_total:
        keys = keys[:n_total]
    quota, acc = {}, 0
    for k in keys:
        q = max(1, int(round(n_total * len(strata[k]) / len(rows))))
        quota[k] = q
        acc += q
    # 配平到 n_total
    i = 0
    while acc > n_total:
        k = keys[i % len(keys)]
        if quota[k] > 1:
            quota[k] -= 1
            acc -= 1
        i += 1
    out, plan = [], []
    for k in keys:
        pick = rng.sample(strata[k], min(quota[k], len(strata[k])))
        out += pick
        plan.append({"stratum": k, "size": len(strata[k]), "sampled": len(pick)})
    return out, plan


def cmd_make(args):
    rows = _load_jsonl(args.src)
    opinion = _load_json(args.opinion) or {}
    id2label, stances_cfg, rules = build_id2label(args.opinion, args.src, args.stances, args.rules)
    dist = opinion.get("stance_distribution") or []
    all_stances = list(stances_cfg.keys()) or [d.get("stance") for d in dist]

    items, plan_all = [], []
    rng = random.Random(args.seed)

    if args.kind == "stance":
        for st in all_stances:
            pool = [r for r in rows if id2label.get((r.get("platform"), str(r.get("id") or ""))) == st]
            if not pool:
                plan_all.append({"stance": st, "size": 0, "sampled": 0,
                                 "note": "该立场无命中记录，无法抽检"})
                continue
            pick, plan = sample_strata(pool, lambda r: "%s/%s" % (r.get("platform"), _band(r)),
                                       min(args.n, len(pool)), rng)
            plan_all.append({"stance": st, "size": len(pool), "sampled": len(pick), "strata": plan})
            for r in pick:
                items.append({
                    "stance": st, "platform": r.get("platform"), "type": r.get("type"),
                    "id": str(r.get("id") or ""), "likes": _likes(r),
                    "content": (r.get("content") or "")[:220],
                    "_q": "这条真的属于「%s」吗？填 verdict=对/错" % st,
                    "verdict": "",
                })
    elif args.kind == "rule":
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import opinion as op
        hit = []
        for r in rows:
            s, h = op.classify_rules(r.get("content", "") or "", rules)
            if s:
                hit.append((s, h, r))
        if not hit:
            print("[spotcheck] 规则层零命中：无法抽检（检查 --rules 是否传入）")
            return 2
        by_st = collections.defaultdict(list)
        for s, h, r in hit:
            by_st[s].append((h, r))
        for st, lst in by_st.items():
            pick = rng.sample(lst, min(args.n, len(lst)))
            plan_all.append({"stance": st, "size": len(lst), "sampled": len(pick)})
            for h, r in pick:
                items.append({
                    "stance": st, "platform": r.get("platform"), "type": r.get("type"),
                    "id": str(r.get("id") or ""), "likes": _likes(r),
                    "content": (r.get("content") or "")[:220],
                    "matched": h,
                    "_q": "规则命中项：这条真的属于「%s」吗？填 verdict=对/错" % st,
                    "verdict": "",
                })
    elif args.kind == "unclassified":
        pool = [r for r in rows
                if id2label.get((r.get("platform"), str(r.get("id") or ""))) in OTHER_NAMES]
        pool_total = sum(1 for k in id2label if id2label[k] in OTHER_NAMES)
        if not pool:
            print("[spotcheck] 未识别池为空：无需抽检（检查 --stances/--opinion 是否与聚类一致）")
            return 2
        if pool_total != len(pool):
            print("[spotcheck] 提示：未识别记录含重复签名 %d 条（池内记录 %d / 唯一签名 %d），"
                  "配额按记录数分配" % (len(pool) - pool_total, len(pool), pool_total))
        pick, plan = sample_strata(pool, lambda r: "%s/%s" % (r.get("platform"), _band(r)),
                                   min(args.n, len(pool)), rng)
        share = collections.Counter()
        for r in rows:
            share[r.get("platform")] = share.get(r.get("platform"), 0) + 1
        plan_all.append({"pool": len(pool), "pool_unique_signatures": pool_total, "sampled": len(pick),
                         "strata": plan, "platform_share_all": dict(share)})
        for r in pick:
            items.append({
                "platform": r.get("platform"), "type": r.get("type"),
                "id": str(r.get("id") or ""), "likes": _likes(r),
                "content": (r.get("content") or "")[:220],
                "_q": "这条含立场表达吗？含则填 has_stance=true 并在 stance 写立场名；不含填 false",
                "has_stance": None, "stance": "",
            })
    else:
        print("[spotcheck] 未知 --kind：%s" % args.kind)
        return 2

    obj = {
        "kind": args.kind,
        "status": "待判读",
        "seed": args.seed,
        "src": args.src,
        "opinion": args.opinion,
        "stances": all_stances,
        "sampling_plan": plan_all,
        "n_items": len(items),
        "items": items,
        "how_to": "逐条把结论填回本文件的 items：stance/rule 核填 verdict=对|错；"
                  "unclassified 核填 has_stance=true|false（true 时同时填 stance）。"
                  "全部填完后运行 --apply。",
    }
    _save_json(args.out, obj)
    md = args.out.rsplit(".", 1)[0] + ".md"
    with io.open(md, "w", encoding="utf-8") as f:
        f.write(_render_form(obj))
    print("[spotcheck] %s 核：抽 %d 条 → %s（人读版 %s）" % (args.kind, len(items), args.out, md))
    print("[spotcheck] 抽样计划：%s" % json.dumps(plan_all, ensure_ascii=False)[:300])
    return 0


def _render_form(obj):
    L = ["# 人工抽检判读单（%s 核）" % obj["kind"], "",
         "> seed=%s ｜ 共 %d 条 ｜ 判完把结论填回同名 .json 的 items[].verdict / items[].has_stance，再跑 --apply"
         % (obj["seed"], obj["n_items"]), ""]
    for i, it in enumerate(obj["items"], 1):
        L.append("### %d. [%s/%s 赞%s] %s" % (i, it.get("platform"), it.get("type"), it.get("likes"),
                                             it.get("stance") or "（未分类）"))
        L.append("")
        L.append("```")
        L.append(it["content"].replace("\n", " "))
        L.append("```")
        L.append("")
        L.append("- 问题：%s" % it["_q"])
        if "matched" in it:
            L.append("- 规则命中：%s" % " / ".join(it["matched"]))
        L.append("- 判读：`%s`" % ("verdict = ___" if obj["kind"] != "unclassified"
                                   else "has_stance = ___ / stance = ___"))
        L.append("")
    return "\n".join(L)


def cmd_apply(args):
    obj = _load_json(args.out)
    if not obj:
        print("[spotcheck] 找不到判读单：%s（先跑 --make）" % args.out)
        return 2
    items = obj.get("items") or []
    missing = []
    for i, it in enumerate(items, 1):
        if obj["kind"] == "unclassified":
            if it.get("has_stance") is None:
                missing.append(i)
        elif not str(it.get("verdict") or "").strip():
            missing.append(i)
    if missing:
        print("[spotcheck] 判读未完成：item #%s 仍为空（缺 %d/%d）——"
              "脚本不产出部分结果，请补齐后重跑 --apply" % (missing[:8], len(missing), len(items)))
        return 2

    result = {"kind": obj["kind"], "seed": obj["seed"], "n_items": len(items)}
    if obj["kind"] in ("stance", "rule"):
        per = collections.defaultdict(lambda: {"n": 0, "wrong": 0, "items": []})
        for it in items:
            st = it.get("stance")
            per[st]["n"] += 1
            bad = str(it["verdict"]).strip() not in ("对", "correct", "true", "是", "1")
            if bad:
                per[st]["wrong"] += 1
                per[st]["items"].append({"platform": it.get("platform"), "likes": it.get("likes"),
                                         "content": it.get("content", "")[:90],
                                         "note": it.get("note", "")})
        n_all = sum(v["n"] for v in per.values())
        w_all = sum(v["wrong"] for v in per.values())
        result["per_stance"] = {k: {"n": v["n"], "wrong": v["wrong"],
                                    "error_rate": round(v["wrong"] / v["n"] * 100, 1),
                                    "examples": v["items"][:3],
                                    "usable_as_main": v["wrong"] / v["n"] <= 0.3}
                                for k, v in sorted(per.items())}
        result["overall_error_rate"] = round(w_all / n_all * 100, 1) if n_all else 0
        result["unreliable_stances"] = [k for k, v in result["per_stance"].items()
                                        if not v["usable_as_main"]]
    else:
        n = len(items)
        with_stance = [it for it in items if it.get("has_stance")]
        by = collections.Counter((it.get("stance") or "（未填）").strip() for it in with_stance)
        p = len(with_stance) / n
        se = (p * (1 - p) / n) ** 0.5 if n else 0
        pool = next((x.get("pool") for x in (obj.get("sampling_plan") or []) if x.get("pool")), 0)
        result["with_stance"] = len(with_stance)
        result["without_stance"] = n - len(with_stance)
        result["p_with_stance"] = round(p * 100, 1)
        result["ci95_pp"] = round(1.96 * se * 100, 1)
        result["estimated_with_stance_in_pool"] = int(round(pool * p))
        result["estimated_total_range"] = [max(0, int(round(pool * p - 1.96 * se * pool))),
                                           min(pool, int(round(pool * p + 1.96 * se * pool)))]
        result["stance_mix_in_sample"] = dict(by.most_common())
        result["pool_size"] = pool
    result["status"] = "已完成"
    obj.update(result)
    obj["status"] = "已完成"
    _save_json(args.out, obj)

    # 回写 opinion.json：抽检结论必须落在聚类工件上，报告与门禁都读它
    op_path = obj.get("opinion")
    opinion = _load_json(op_path)
    if opinion:
        sc = opinion.setdefault("spotcheck", {})
        sc[obj["kind"]] = result
        sc["_note"] = ("人工抽检结论（spotcheck.py --apply 写入）。纪律：未抽检的标签不得作为结论依据；"
                       "误判率 >30% 的立场不得出现在主立场/主流/压倒性表述里；未分类段的估计必须与占比并列披露。")
        _save_json(op_path, opinion)
        print("[spotcheck] 已回写 %s 的 spotcheck.%s" % (op_path, obj["kind"]))

    print("[spotcheck] %s 核完成：%s" % (obj["kind"], json.dumps(
        {k: v for k, v in result.items() if k not in ("per_stance",)},
        ensure_ascii=False)[:400]))
    return 0


def cmd_report(args):
    """把抽检结论渲染成报告章节用的 markdown 片段。"""
    obj = _load_json(args.out)
    if not obj or obj.get("status") != "已完成":
        print("[spotcheck] 尚无已完成结论：%s" % args.out)
        return 2
    L = []
    if obj["kind"] in ("stance", "rule"):
        L.append("**抽检口径**：seed=%s，共判读 %d 条；每立场按平台×点赞档配额抽样。" % (obj["seed"], obj["n_items"]))
        L.append("")
        L.append("| 立场 | 抽样 | 误判 | 误判率 | 可否作为主结论 |")
        L.append("| --- | --- | --- | --- | --- |")
        for k, v in (obj.get("per_stance") or {}).items():
            L.append("| %s | %d | %d | %.1f%% | %s |" % (k, v["n"], v["wrong"], v["error_rate"],
                                                           "可" if v["usable_as_main"] else "**不可**（>30%）"))
        L.append("")
        if obj.get("unreliable_stances"):
            L.append("⚠ **以下立场误判率 >30%%，不得作为主结论呈现**：%s。"
                     % "、".join(obj["unreliable_stances"]))
            L.append("")
    else:
        L.append("**未分类段抽样**：从未识别层 %d 条（= 纯反应 + 未分类，与报告口径行同源）中按平台×点赞档"
                 "抽 %d 条人工判读，其中 **%d 条含立场表达（%.1f%%）**，%d 条不含（纯反应/玩笑/信息）。"
                 % (obj.get("pool_size", 0), obj["n_items"], obj["with_stance"], obj["p_with_stance"],
                    obj["without_stance"]))
        L.append("")
        L.append("按层配额加权回推：**未识别层约 %d 条含立场表达**（95%% 置信区间 %s 条），"
                 "即全量语料里还有约 %d 条立场表达未进入占比——故本章占比应读作"
                 "「已识别部分内的结构」，其绝对水平被系统性低估。"
                 % (obj["estimated_with_stance_in_pool"],
                    "%d–%d" % tuple(obj["estimated_total_range"]),
                    obj["estimated_with_stance_in_pool"]))
        L.append("")
        L.append("样本内立场构成：%s" % "、".join("%s %d 条" % (k, v) for k, v in
                                                  (obj.get("stance_mix_in_sample") or {}).items()))
        L.append("")
    print("\n".join(L))
    out = args.md or (args.out.rsplit(".", 1)[0] + "_结论.md")
    with io.open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("[spotcheck] 结论片段 → %s" % out)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True, choices=["stance", "rule", "unclassified"])
    ap.add_argument("--in", dest="src", required=True, help="分析语料（data.relevant.jsonl）")
    ap.add_argument("--opinion", default="", help="opinion.json（读立场分布；--apply 时回写抽检结论）")
    ap.add_argument("--stances", default="", help="stances.json（重算标签用；缺省取 --opinion 同目录）")
    ap.add_argument("--rules", default="", help="opinion_rules.json（rule 核必须传）")
    ap.add_argument("--out", required=True, help="data/{event_id}/spotcheck.json")
    ap.add_argument("--n", type=int, default=10, help="每立场抽样条数（默认 10；unclassified 核=总条数）")
    ap.add_argument("--seed", type=int, default=2026, help="抽样种子（固定值保证可复现）")
    ap.add_argument("--make", action="store_true", help="生成判读单")
    ap.add_argument("--apply", action="store_true", help="读回判读结果并计算")
    ap.add_argument("--md", default="", help="--report 输出路径")
    ap.add_argument("--report", action="store_true", help="渲染结论 markdown 片段")
    args = ap.parse_args()
    def _find(name):
        """按 语料目录 → 其父目录 → opinion 目录 → 其父目录 顺序找工件。

        联合任务里语料在 normalized/ 而 stances.json 常放在事件目录（上一级），
        故必须向上找一层——找不到就会静默把所有记录判成"未识别"，那是灾难性的错。
        """
        cands = []
        for p in (args.src, args.opinion):
            if not p:
                continue
            d = os.path.dirname(os.path.abspath(p))
            cands += [d, os.path.dirname(d)]
        seen = set()
        for d in cands:
            if not d or d in seen:
                continue
            seen.add(d)
            f = os.path.join(d, name)
            if os.path.exists(f):
                return f
        return ""
    if not args.stances:
        args.stances = _find("stances.json")
    if not args.rules:
        args.rules = _find("opinion_rules.json")
    if not args.stances:
        print("[spotcheck] 错误：找不到 stances.json（标签会全部落进未识别）。"
              "请用 --stances 指定，或确保它与 --in/--opinion 同目录。")
        return 2
    if args.make:
        return cmd_make(args)
    if args.apply:
        return cmd_apply(args)
    if args.report:
        return cmd_report(args)
    print("[spotcheck] 请指定 --make / --apply / --report 之一")
    return 2


if __name__ == "__main__":
    sys.exit(main())
