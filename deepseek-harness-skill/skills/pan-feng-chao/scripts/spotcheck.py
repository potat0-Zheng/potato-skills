# -*- coding: utf-8 -*-
"""pan-feng-chao 人工抽检（v0.5）：把「必须抽检」从口号变成工件。

背景：四个技能都要求「分类结果必须抽检才能引用」，但此前没有工件承接——
没有输入格式、没有落盘位置、报告里也没位置显示，于是抽检永远被跳过，
报告里的占比始终是一个裸数字。

本脚本做三件事：
  1. `--make`  分层配额抽样，生成判读单（spotcheck.json 待填 + spotcheck.md 人读）
  2. `--apply` 读回判读结果，计算误判率 / 分层加权回推，并把结论写回 opinion.json
  3. `--report`（可选）把结论渲染成报告章节用的 markdown

三种核（`--kind`）：
  stance       立场标签核：每个立场抽 N 条，判「这条真的属于该立场吗」（对/错）
  rule         规则层核：只抽规则层命中项，同上（正则只能命中「像」某立场的表述，误判率高是预期）
  unclassified 未分类段核：判「这条含立场表达吗」，含则选立场；按分层配额加权回推到全段

分层维度 = 平台 × 点赞档（× 立场，仅 stance 核）。固定 --seed 保证任何人可复现同一批样本。

v0.5 变更（对应写入计划 §4 的 D 系列）：
  · D5  抽检池 ≡ `opinion.residual`（个人 UGC 未识别）。旧实现 build_id2label 不做机构判定，
        于是 unclassified 池把机构帖也算进来（实测 residual.count=1 而 pool=2）：报告的
        「含立场表达」回推用了更大的分母，文案却写「与报告口径行同源」。stance/rule 核同样
        排除机构帖——口径与 A 层一致。
  · D13 池与 residual 不一致时**默认硬失败（exit 2）**，只有显式 --allow-pool-mismatch 才降级为
        披露；并透传 --institutional（否则聚类用自定义规则、抽检用内置默认，池会再次不同源）。
  · D14 抽样条数与其他阈值真读 config.json（命令行 > config > 内置默认）。
  · 新增统计推断：未分类段估计给 Wilson 置信区间、后分层加权与设计效应（stats.py）。
  · 新增双人判读与编码信度：--coder 标记判读人；--coder-b 传入第二份判读单后，计算
    一致率 / Cohen κ / Krippendorff α，并把不一致条目列成仲裁队列。
  · 新增 pipeline 披露：默认路径零第三方依赖，可选增强缺失时降级并写进工件。

判读纪律（脚本强制）：
  · 代表性样本（core）逐条必须填 verdict / has_stance，缺任何一条 → --apply 失败，不产出「部分结果」
  · 富集样本（enrich）可留空：它不参与任何估计，未判完只影响富集对照，**不阻塞 --apply**（v0.6.1，P4）
  · 未抽检（status != 已完成）时，报告只能把占比读作「未校正」；门禁 G11 会拦

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

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import enhance  # noqa: E402  同目录共用模块：可选增强探测（零硬依赖）
import manifest as manifest_mod  # noqa: E402  同目录共用模块：工件指纹
import stats    # noqa: E402  同目录共用模块：纯标准库统计
import structure  # noqa: E402  同目录共用模块：同源文本检测（纯标准库，用于确定度评分）

# 与 opinion.py 保持一致的读法（不 import，避免脚本互依赖）
OTHER_NAMES = {"未识别", "无关/其他"}

# ---- config.json：阈值集中处（v0.5，D14）优先级：命令行 > config.json > 内置默认 ----
CONFIG_DEFAULTS = {
    "spotcheck_n": 10,      # 每种核的抽样条数
    "seed": 2026,           # 抽样种子（固定值保证可复现）
    "error_rate_max": 30,   # 误判率上限（百分点）：超过则该立场不得作为主结论
    "coverage_target": 70,  # 覆盖率目标（百分点），仅用于结论措辞告警
}


def load_config():
    """读本 skill 的 config.json。缺失/损坏时退回内置默认，并在产物中披露来源。"""
    cfg = dict(CONFIG_DEFAULTS)
    cfg["_source"] = "builtin_default"
    cfg["_path"] = os.path.join(SKILL_DIR, "config.json")
    cfg["_from_config"] = []
    if os.path.exists(cfg["_path"]):
        try:
            with io.open(cfg["_path"], encoding="utf-8") as f:
                user = json.load(f)
            if isinstance(user, dict):
                for k in CONFIG_DEFAULTS:
                    if k in user and user[k] is not None:
                        cfg[k] = user[k]
                        cfg["_from_config"].append(k)
                cfg["_source"] = "config.json"
        except Exception as e:
            cfg["_error"] = str(e)
    return cfg


def pick(cli_value, cfg, key):
    """阈值取值：命令行 > config.json > 内置默认。返回 (值, 来源标签)。"""
    if cli_value is not None:
        return cli_value, "cli"
    if key in (cfg.get("_from_config") or []):
        return cfg.get(key), "config.json"
    return CONFIG_DEFAULTS.get(key), "builtin_default"


def _likes(r):
    return int(((r.get("metrics") or {}).get("likes", 0) or 0))


def _key_of(r):
    """记录签名：(platform, id)。全脚本统一用它，避免各处写法不一致导致池对不上。"""
    return (r.get("platform"), str(r.get("id") or ""))


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


def build_id2label(opinion_path, src_path, stances, rules_path="", institutional_path=""):
    """用 opinion.py 的同一套判据重算全量标签，避免「抽样样本与聚类结果不一致」。

    返回 (id2label, stances_cfg, rules, inst_cfg, inst_keys)；id2label 的键是 (platform, id)。
    v0.5（D5）：**机构帖（B 层）不进 id2label**（记在 inst_keys 里），于是它不会落进任何抽检池 ——
    口径与 A 层一致。旧实现不做机构判定，unclassified 池因此含机构帖（residual=1 而 pool=2），
    结果会请人工对一条媒体帖判「这条含立场表达吗」，把信息供给方与舆论主体混为一谈。
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import opinion as op
    # 必须走 opinion.load_stances()：它会做 _clean_stances()（只保留"值为非空字符串列表"的项）。
    # 直接用 json 原始字典会把 stances.json 的**非列表键**（如 intensity 词表、_readme 说明）
    # 当成立场，而字符串会被逐字符迭代 → 任何一个字命中就把整条记录判成该"立场"，
    # 导致 unclassified 池与 residual 不同源（实测：池 4 ≠ residual 11）。这是 v0.8 修复。
    stances_cfg = op.load_stances(stances) if stances else {}
    rules, _ = op.load_rules(rules_path) if rules_path else ({}, [])
    inst_cfg = op.load_institutional(institutional_path)
    sig, inst_keys, seen = {}, set(), set()
    for r in _load_jsonl(src_path):
        k = (r.get("platform"), str(r.get("id") or ""))   # 唯一签名只判一次（同帖多评不重复判）
        if k in seen:
            continue
        seen.add(k)
        if op.is_institutional(r, inst_cfg)[0]:
            inst_keys.add(k)
            continue
        s, _, _ = op.classify(r.get("content", "") or "", stances_cfg)
        sig[k] = s
    return sig, stances_cfg, rules, inst_cfg, inst_keys


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


def _pipeline(rules=None):
    """管道披露（v0.5）：默认路径零第三方依赖；可选增强缺失时降级并写进工件。"""
    p = enhance.manifest(requested=[], used=[])
    p["layers"] = ["dict", "manual"] + (["rule"] if rules else [])
    p["note"] = ("默认路径只用标准库。本脚本本身不做立场判定：标签口径来自 opinion.py 的词典/规则层，"
                 "『manual』表示结论里含人的判读——人的判读其信度由 --coder-b 的一致性统计（κ/α）披露，"
                 "未提供第二判读人时该字段为 null，不得默认当作「判读可靠」。")
    return p


def _day_boxes(rows, use_time=True, max_boxes=3):
    """时间分层：把语料日期等分成箱（默认最多 3 箱）映射为 D1/D2/D3。

    为什么分时间层：事件爆发日与尾声日的立场结构完全不同，纯随机抽样可能整箱漏抽
    （短事件里某一天常占绝大多数记录），"焦点转移"就无从核对。只按**日期**切箱，不引入因果假设。
    """
    if not use_time:
        return {}, False
    days = sorted({str(r.get("time") or "")[:10] for r in rows if r.get("time")})
    if len(days) < 2:
        return {d: "D1" for d in days}, False
    boxes = max(1, min(int(max_boxes), len(days)))
    mapping = {}
    for i, d in enumerate(days):
        mapping[d] = "D%d" % (min(boxes - 1, int(i * boxes / len(days))) + 1)
    return mapping, True


def _stratum_key(rec, day_boxes, use_time):
    """估计用的分层键：平台 × 点赞档（× 时间箱）。--make 与 --apply 必须用同一个键。"""
    parts = [str(rec.get("platform") or ""), _band(rec)]
    if use_time:
        parts.append((day_boxes or {}).get(str(rec.get("time") or "")[:10], "D?"))
    return "/".join(parts)


def _certainty_of(rec, stances, rules, llm_labels, dup_keys, key, op=None,
                  llm_conf=None, llm_labels_b=None, llm_conf_min=0.7):
    """确定度评分：汇总"最可能被机器判错"的信号（用于分歧优先的富集样本）。

    信号（v0.7 起含 LLM 与反讽两类）：
      · 仅命中 1 个立场词、命中词疑似被否定修饰、文本过短；
      · 词典层与规则层不一致；词典层与 LLM 层不一致；**LLM 置信度低**；**两个 LLM 模型互不一致**；
      · 文本落在同源文本簇内。
    低确定度的记录**只进富集块**（查错用），不进代表性样本——所以它不会污染占比估计。
    """
    if op is None:
        import opinion as op  # noqa: PLC0415
    text = rec.get("content") or ""
    label, matched, negated = op.classify(text, stances or {})
    reasons = []
    if label != op.OTHER and len(matched) <= 1:
        reasons.append("仅命中 1 个立场词")
    if negated:
        reasons.append("命中词疑似被否定修饰")
    irony = op._irony_signals(text, matched)
    if irony:
        reasons.append("疑似反讽/否定（线索：%s）" % "、".join(irony))
    if rules:
        rl, _rh = op.classify_rules(text, rules)
        if rl and rl != label:
            reasons.append("规则层与词典层不一致（规则层判为 %s）" % rl)
    if llm_labels:
        st = llm_labels.get(key)
        if st and st != label:
            reasons.append("LLM 层与词典层不一致（LLM 判为 %s）" % st)
        if llm_labels_b:
            st_b = llm_labels_b.get(key)
            if st and st_b and st != st_b:
                reasons.append("两个 LLM 模型互相不一致（%s vs %s）" % (st, st_b))
    if llm_conf:
        c = llm_conf.get(key)
        if c is not None and float(c) < float(llm_conf_min):
            reasons.append("LLM 置信度低（%.2f < %.2f）" % (float(c), float(llm_conf_min)))
    if key in (dup_keys or set()):
        reasons.append("文本落在同源文本簇内")
    if 0 < len(text.strip()) < 10:
        reasons.append("文本过短（<10 字）")
    return ("low" if reasons else "high"), reasons


def _collapse_strata(rows, day_boxes, use_time, cap):
    """把细碎层合并到上限 cap 层（survey 里的 stratum collapsing）。

    为什么必须做：分层维度一多（平台 × 点赞档 × 时间箱），小语料会碎成比样本数还多的层，
    此时按"每层至少 1 条"分配等于每层抽 1 条，估计失去意义；而不合并还会让大量层
    永远抽不到（成为隐性偏倚）。做法是保留最大的 cap−1 层，其余合并为「其他/合并层」，
    合并后仍按层大小加权（后分层），偏差可控、方差略增——这是标准取舍，且全程披露。
    """
    base = collections.Counter(_stratum_key(r, day_boxes, use_time) for r in rows)
    cap = max(1, int(cap))
    if len(base) <= cap:
        return {_key_of(r): _stratum_key(r, day_boxes, use_time) for r in rows}, False
    keep = [k for k, _c in base.most_common(max(1, cap - 1))]
    kept = set(keep)
    out = {}
    for r in rows:
        k = _stratum_key(r, day_boxes, use_time)
        out[_key_of(r)] = k if k in kept else "其他/合并层"
    return out, True


def _common_fields(rec, block, day_boxes, use_time, certainty, reasons, stratum=None):
    """判读单条目的公共字段（v0.6：块标记、确定度、作者、时间箱、分层键）。"""
    return {
        "block": block,
        "certainty": certainty,
        "uncertainty_reasons": reasons,
        "author": str(rec.get("author") or ""),
        "day_box": (day_boxes or {}).get(str(rec.get("time") or "")[:10], "") if use_time else "",
        "stratum": stratum if stratum is not None else _stratum_key(rec, day_boxes, use_time),
        "platform": rec.get("platform"), "type": rec.get("type"),
        "id": str(rec.get("id") or ""), "likes": _likes(rec),
        "content": (rec.get("content") or "")[:220],
    }


def status(path, pending=False):
    """一屏状态（≤25 行）：**agent 用这个看判读单，而不是整体 read JSON**。

    pending=True 时另列待判读条目（ID + 块 + 确定度理由），便于直接补齐。
    """
    obj = _load_json(path)
    if not obj:
        print("[spotcheck] --status 读取失败：%s" % path)
        return 2
    kb = os.path.getsize(path) / 1024.0
    items = obj.get("items") or []
    core = [it for it in items if (it.get("block") or "core") == "core"]
    enr = [it for it in items if it.get("block") == "enrich"]

    def _missing(it):
        if obj.get("kind") == "unclassified":
            return it.get("has_stance") is None
        return not str(it.get("verdict") or "").strip()

    miss = [it for it in items if _missing(it)]
    L = ["[spotcheck --status] kind=%s ｜ 状态=%s ｜ seed=%s ｜ 判读人=%s ｜ 工件 %.1f KB"
         % (obj.get("kind"), obj.get("status"), obj.get("seed"), obj.get("coder") or "（未标）", kb),
         "块：core %d ｜ enrich %d ｜ 合计 %d" % (len(core), len(enr), len(items)),
         "待判读：%d 条（core %d · enrich %d）%s"
         % (len(miss), len([i for i in miss if (i.get("block") or "core") == "core"]),
            len([i for i in miss if i.get("block") == "enrich"]),
            " → 补齐后跑 --apply" if miss else " → 可直接 --apply")]
    plan = obj.get("sampling_plan") or [{}]
    p0 = plan[0] if plan else {}
    if p0.get("pool") is not None:
        L.append("池：%s ｜ opinion.residual=%s %s"
                 % (p0.get("pool"), p0.get("pool_expected_from_opinion"),
                    "✓ 同源" if p0.get("pool_mismatch") is None else "⚠ 不一致（已显式允许）"))
    ts = obj.get("time_strata") or {}
    if ts.get("day_boxes"):
        L.append("时间分层：%d 箱（%s）｜ 实际层数 %d%s"
                 % (len(set(ts["day_boxes"].values())), "、".join(sorted(set(ts["day_boxes"].values()))),
                    len(p0.get("strata") or []), "（碎层已合并）" if p0.get("strata_collapsed") else ""))
    if p0.get("enrich_reasons"):
        L.append("富集理由：%s" % "、".join("%s×%d" % (k, v) for k, v in p0["enrich_reasons"].items()))
    if obj.get("status") == "已完成":
        if obj.get("per_stance"):
            L.append("结论：总体误判率 %s%% ｜ 各立场 %s"
                     % (obj.get("overall_error_rate"),
                        "；".join("%s %s%%%s" % (k, v.get("error_rate"),
                                                "" if v.get("usable_as_main") else "（不可作主结论）")
                                 for k, v in obj["per_stance"].items())))
        elif obj.get("p_with_stance") is not None:
            L.append("结论：含立场表达 %s%%（95%%CI %s）｜ 分层加权 %s%% ｜ 回推约 %s 条"
                     % (obj.get("p_with_stance"), stats.fmt_ci(obj.get("p_with_stance_ci95")),
                        (obj.get("stratified") or {}).get("p_with_stance"),
                        obj.get("estimated_with_stance_in_pool")))
        e = obj.get("enrich") or {}
        if e.get("n_enrich"):
            L.append("富集对照（非代表性）：富集 %s%% vs 核心 %s%%（差 %s pp）→ %s"
                     % (e.get("enrich_rate_pct"), e.get("core_rate_pct"), e.get("diff_pp"),
                        e.get("verdict")))
        ic = obj.get("intercoder") or {}
        L.append("信度：%s%s" % (ic.get("status") or "未提供第二判读人",
                                "" if ic.get("status") != "已完成" else
                                " ｜ κ %s ｜ α %s" % (ic.get("cohen_kappa"), ic.get("krippendorff_alpha"))))
    L.append("读取建议：本文件 %.1f KB；用本命令取状态，勿整体读取 JSON；结论片段用 --report" % kb)
    print("\n".join(L))
    if pending:
        if not miss:
            print("--- 无待判读条目 ---")
        else:
            print("--- 待判读条目（最多 40 条）---")
            for it in miss[:40]:
                print("%s/%s\t%s\t%s\t%s"
                      % (it.get("platform"), it.get("id"), it.get("block") or "core",
                         it.get("certainty") or "-",
                         "、".join(it.get("uncertainty_reasons") or []) or "-"))
            if len(miss) > 40:
                print("…另有 %d 条" % (len(miss) - 40))
    return 0


def cmd_make(args):
    rows = _load_jsonl(args.src)
    opinion = _load_json(args.opinion) or {}
    id2label, stances_cfg, rules, inst_cfg, inst_keys = build_id2label(
        args.opinion, args.src, args.stances, args.rules, args.institutional)
    if inst_keys:
        print("[spotcheck] 机构帖 %d 条已排除在所有抽检池外（与 A 层同口径；v0.5 D5）" % len(inst_keys))
    dist = opinion.get("stance_distribution") or []
    all_stances = list(stances_cfg.keys()) or [d.get("stance") for d in dist]

    items, plan_all = [], []
    rng = random.Random(args.seed)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import opinion as op

    # 两个抽样块（v0.6）：
    #   core   —— 代表性样本：按 平台 × 点赞档 × 时间箱 分层配额随机抽，**只有它参与估计**
    #   enrich —— 富集样本：把低确定度记录尽量全抽，**只用于查错**，明确标注非代表性
    # 为什么不把整池按"分歧"加权：代表性样本一旦被加权改变，占比估计就不再无偏。
    # "查错"要挑难的，"估数"要像全体——两件事必须分开做（两阶段抽样的思路）。
    use_time = not args.no_time_strata
    day_boxes, time_used = _day_boxes(rows, use_time=use_time)
    personal = [r for r in rows if _key_of(r) not in inst_keys]
    dup_map = structure.near_dup_map(personal, _key_of, lambda r: r.get("content") or "",
                                     threshold=args.dup_threshold)
    dup_keys = set(dup_map.get("flags") or {})
    llm_labels, _llm_rows, llm_conf = op.load_llm_labels(getattr(args, "llm_labels", "") or "")
    llm_labels_b, _llm_rows_b, _llm_conf_b = op.load_llm_labels(getattr(args, "llm_labels_b", "") or "")
    if dup_keys:
        print("[spotcheck] 同源文本 %d 条（仅用于确定度评分；不动抽样池与分母）" % len(dup_keys))
    if time_used:
        print("[spotcheck] 时间分层已启用：%s" % json.dumps(day_boxes, ensure_ascii=False))

    def _cert_map(pool):
        return {_key_of(r): _certainty_of(r, stances_cfg, rules, llm_labels, dup_keys, _key_of(r),
                                          op=op, llm_conf=llm_conf, llm_labels_b=llm_labels_b,
                                          llm_conf_min=float(getattr(args, "llm_conf_min", 0.7)))
                for r in pool}

    def _block_split(pool, n_core, cert):
        # 层上限：样本数的一半（至少 2 层），避免碎层导致"每层抽 1 条"
        cap = max(2, int(round(max(2, int(n_core)) / 2.0)))
        lab, collapsed = _collapse_strata(pool, day_boxes, time_used, cap)
        core, plan = sample_strata(pool, lambda r: lab[_key_of(r)],
                                   max(0, min(int(n_core), len(pool))), rng)
        for s in plan:
            s["collapsed"] = collapsed
        core_keys = set(_key_of(r) for r in core)
        low, reasons = [], collections.Counter()
        for r in pool:
            k = _key_of(r)
            if k in core_keys or cert[k][0] != "low":
                continue
            low.append((cert[k][1], r))
            for x in cert[k][1]:
                reasons[x] += 1
        low.sort(key=lambda t: (-len(t[0]), -_likes(t[1])))   # 理由越多、热度越高越先抽
        return core, plan, [r for _rs, r in low[:max(0, args.enrich_n)]], reasons, lab, collapsed

    def _plan_entry(pool, core, plan, enrich, reasons, n_asked, stance="", collapsed=False):
        _ney, _cmp = stats.neyman_allocation([s["size"] for s in plan], max(1, int(n_asked or 1)))
        entry = {
            "size": len(pool), "sampled": len(core), "sampled_enrich": len(enrich),
            "blocks": {"core": len(core), "enrich": len(enrich)},
            "strata": plan,
            "strata_collapsed": collapsed,
            "enrich_reasons": dict(reasons.most_common()),
            "neyman_diagnostic": _cmp,
            "required_n_for_margin": {"±10pp": stats.required_n(0.5, 0.10, N=len(pool)),
                                      "±5pp": stats.required_n(0.5, 0.05, N=len(pool))},
        }
        if stance:
            entry["stance"] = stance
        return entry

    if args.kind == "stance":
        for st in all_stances:
            pool = [r for r in rows if id2label.get(_key_of(r)) == st]
            if not pool:
                plan_all.append({"stance": st, "size": 0, "sampled": 0,
                                 "note": "该立场无命中记录，无法抽检"})
                continue
            cert = _cert_map(pool)
            core, plan, enrich, reasons, lab, collapsed = _block_split(pool, args.n, cert)
            plan_all.append(_plan_entry(pool, core, plan, enrich, reasons, args.n, stance=st,
                                        collapsed=collapsed))
            for r in core:
                c, rs = cert[_key_of(r)]
                it = _common_fields(r, "core", day_boxes, time_used, c, rs, stratum=lab[_key_of(r)])
                it.update({"stance": st, "_q": "这条真的属于「%s」吗？填 verdict=对/错" % st,
                           "verdict": ""})
                items.append(it)
            for r in enrich:
                c, rs = cert[_key_of(r)]
                it = _common_fields(r, "enrich", day_boxes, time_used, c, rs, stratum=lab[_key_of(r)])
                it.update({"stance": st, "verdict": "",
                           "_q": "【富集·低确定度】这条真的属于「%s」吗？填 verdict=对/错" % st,
                           "note": "富集样本：只用于查错，不代表该立场的分布，不计入误判率分母"})
                items.append(it)
    elif args.kind == "rule":
        hit = []
        for r in rows:
            if op.is_institutional(r, inst_cfg)[0]:     # D5：机构帖不进池（口径与 A 层一致）
                continue
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
            pool = [r for _h, r in lst]
            cert = _cert_map(pool)
            core, plan, enrich, reasons, lab, collapsed = _block_split(pool, args.n, cert)
            plan_all.append(_plan_entry(pool, core, plan, enrich, reasons, args.n, stance=st,
                                        collapsed=collapsed))
            for r in core:
                h = next(hh for hh, rr in lst if rr is r)
                c, rs = cert[_key_of(r)]
                it = _common_fields(r, "core", day_boxes, time_used, c, rs, stratum=lab[_key_of(r)])
                it.update({"stance": st, "matched": h, "verdict": "",
                           "_q": "规则命中项：这条真的属于「%s」吗？填 verdict=对/错" % st})
                items.append(it)
            for r in enrich:
                h = next(hh for hh, rr in lst if rr is r)
                c, rs = cert[_key_of(r)]
                it = _common_fields(r, "enrich", day_boxes, time_used, c, rs, stratum=lab[_key_of(r)])
                it.update({"stance": st, "matched": h, "verdict": "",
                           "_q": "【富集·低确定度】规则命中项：这条真的属于「%s」吗？填 verdict=对/错" % st,
                           "note": "富集样本：只用于查错，不计入误判率分母"})
                items.append(it)
    elif args.kind == "unclassified":
        pool = [r for r in rows if id2label.get(_key_of(r)) in OTHER_NAMES]
        pool_total = sum(1 for k in id2label if id2label[k] in OTHER_NAMES)
        if not pool:
            print("[spotcheck] 未识别池为空：无需抽检（检查 --stances/--opinion 是否与聚类一致）")
            return 2
        if pool_total != len(pool):
            print("[spotcheck] 提示：未识别记录含重复签名 %d 条（池内记录 %d / 唯一签名 %d），"
                  "配额按记录数分配" % (len(pool) - pool_total, len(pool), pool_total))
        # D13：抽检池必须与报告口径行的分母同源（pool ≡ opinion.residual.count），
        # 否则回推用的是另一个分母，而文案还写着「与报告口径行同源」。
        residual = (opinion.get("residual") or {}).get("count")
        pool_mismatch = None
        if isinstance(residual, int) and residual != len(pool):
            pool_mismatch = {"pool": len(pool), "residual_count": residual}
            if not args.allow_pool_mismatch:
                print("[spotcheck] 错误：未分类抽检池与 opinion.json 的 residual 不同源"
                      "（pool=%d ≠ residual.count=%d）。" % (len(pool), residual))
                print("[spotcheck] 口径要求「抽检池 ≡ 报告口径行的分母」。常见原因：")
                print("[spotcheck]   ① 语料不是聚类时那一份（--in 传错）")
                print("[spotcheck]   ② 机构判定规则不同（聚类用了自定义 --institutional，这里没传）")
                print("[spotcheck]   ③ stances.json 与聚类时不同（标签整体漂移，池会跟着变）")
                print("[spotcheck] 查明原因后再决定是否用 --allow-pool-mismatch 降级为披露（该决定会写进工件）。")
                return 2
            print("[spotcheck] ⚠ 已允许口径不一致（pool=%d ≠ residual.count=%d）："
                  "结论必须与 opinion.json 的 residual 并排披露。" % (len(pool), residual))
        cert = _cert_map(pool)
        core, plan, enrich, reasons, lab, collapsed = _block_split(pool, args.n, cert)
        share = collections.Counter()
        for r in rows:
            share[r.get("platform")] = share.get(r.get("platform"), 0) + 1
        _entry = _plan_entry(pool, core, plan, enrich, reasons, args.n, collapsed=collapsed)
        _entry.update({"pool": len(pool), "pool_unique_signatures": pool_total,
                       "pool_expected_from_opinion": residual, "pool_mismatch": pool_mismatch,
                       "platform_share_all": dict(share)})
        plan_all.append(_entry)
        for r in core:
            c, rs = cert[_key_of(r)]
            it = _common_fields(r, "core", day_boxes, time_used, c, rs, stratum=lab[_key_of(r)])
            it.update({"has_stance": None, "stance": "",
                       "_q": "这条含立场表达吗？含则填 has_stance=true 并在 stance 写立场名；不含填 false"})
            items.append(it)
        for r in enrich:
            c, rs = cert[_key_of(r)]
            it = _common_fields(r, "enrich", day_boxes, time_used, c, rs, stratum=lab[_key_of(r)])
            it.update({"has_stance": None, "stance": "",
                       "_q": "【富集·低确定度】这条含立场表达吗？含则填 has_stance=true 并写立场名；不含填 false",
                       "note": "富集样本：只用于查错（看机器最容易漏/错在哪一类），"
                               "不代表未识别层分布，不计入回推估计"})
            items.append(it)
    else:
        print("[spotcheck] 未知 --kind：%s" % args.kind)
        return 2

    n_core = sum(1 for it in items if it.get("block") == "core")
    n_enrich = len(items) - n_core
    if n_enrich:
        print("[spotcheck] 抽样块：代表性（core）%d 条 + 富集（低确定度，仅查错）%d 条；"
              "估计只用 core，富集单独报" % (n_core, n_enrich))

    obj = {
        # 状态摘要与读取提示放最前（v0.7）：判读单会随条目数增长，整体读取会占用大量上下文
        "summary": {
            "kind": args.kind, "status": "待判读", "seed": args.seed, "coder": args.coder,
            "n_core": n_core, "n_enrich": n_enrich,
            "time_strata": (day_boxes if time_used else None),
            "read_first": "python spotcheck.py --status --out <本文件>（或 --pending 列待判读条目）",
        },
        "_read_hint": {
            "read_first": "python spotcheck.py --status --out <本文件>；结论片段用 --report",
            "why": "本文件含全部判读条目与抽样计划，体积随抽样量增长；整体读取会占用大量上下文",
            "detail_keys": ["items", "sampling_plan"],
            "machine_consumers": "百宝袋 build_report.py 读回写进 opinion.json 的 spotcheck 块",
        },
        "kind": args.kind,
        "status": "待判读",
        "seed": args.seed,
        "coder": args.coder,
        "src": args.src,
        "opinion": args.opinion,
        "institutional": args.institutional,
        "stances": all_stances,
        "sampling_plan": plan_all,
        # n_items 只数**代表性样本**（core）：它是估计的依据，也是报告里"共判读 N 条"的那个 N。
        # 富集的低确定度样本单列，避免"判了 30 条"被误读成"估计基于 30 条"。
        "n_items": n_core,
        "n_items_enrich": n_enrich,
        "blocks": {"core": n_core, "enrich": n_enrich},
        "time_strata": {"enabled": bool(time_used), "day_boxes": day_boxes},
        "items": items,
        "thresholds": {"n": args.n, "seed": args.seed, "error_rate_max": args.error_rate_max,
                       "n_source": args._n_source, "seed_source": args._seed_source,
                       "config_source": args._cfg_source},
        "pipeline": _pipeline(rules if args.kind == "rule" else None),
        "how_to": "逐条把结论填回本文件的 items：stance/rule 核填 verdict=对|错；"
                  "unclassified 核填 has_stance=true|false（true 时同时填 stance）。"
                  "**代表性样本（block=core）必须全部判完，否则 --apply 失败**；"
                  "富集样本（block=enrich）可选：留空不影响任何估计，只是不出"
                  "「机器最容易错在哪一类」的对照。填完后运行 --apply。",
    }
    if args.coder:
        obj["coder_note"] = ("本判读单的判读人 = %s。双人判读请让第二人用另一份 --out 再跑一次 --make，"
                             "然后 --apply --coder-b <第二份.json> 计算一致率与 κ/α。" % args.coder)
    md = args.out.rsplit(".", 1)[0] + ".md"
    with io.open(md, "w", encoding="utf-8") as f:
        f.write(_render_form(obj))
    # E5.1：判读单指纹（输入语料 / 词表 / 判读单自身都留痕）
    obj["run_manifest"] = manifest_mod.build(
        inputs=[(args.src, True, "corpus"), (args.stances, False, "stances"),
                (args.rules, False, "rules"), (args.institutional, False, "institutional"),
                (args.llm_labels, False, "llm_labels"), (args.llm_labels_b, False, "llm_labels_b")],
        args=args, vocabulary=stances_cfg, script_path=os.path.abspath(__file__),
        reference=args.opinion or None,
        extra={"config_source": args._cfg_source})
    # E5.4：离线点选式判读单（HTML 只是录入界面，JSON 仍是唯一权威）
    if args.html:
        _html_path = args.out.rsplit(".", 1)[0] + ".html"
        _copy = dict(obj)
        _copy["_form_path"] = os.path.basename(args.out)
        with io.open(_html_path, "w", encoding="utf-8") as f:
            f.write(render_html(_copy))
        obj["html"] = _html_path
        print("[spotcheck] 离线点选判读单 → %s（导出 JSON 后仍用 --apply 读取）" % _html_path)
    _save_json(args.out, obj)
    print("[spotcheck] %s 核：代表性样本（core，参与估计）%d 条%s → %s（人读版 %s）"
          % (args.kind, n_core,
             ("＋富集样本（只查错、不参与估计）%d 条" % n_enrich) if n_enrich else "",
             args.out, md))
    print("[spotcheck] 抽样计划：%s" % json.dumps(plan_all, ensure_ascii=False)[:300])
    try:
        print("[spotcheck] 判读单 %.1f KB；看状态用 `python spotcheck.py --status --out %s`"
              "（勿整体读取 JSON）" % (os.path.getsize(args.out) / 1024.0, os.path.basename(args.out)))
    except OSError:
        pass
    return 0


def _render_form(obj):
    _blk = obj.get("blocks") or {}
    L = ["# 人工抽检判读单（%s 核）" % obj["kind"], "",
         "> seed=%s ｜ 判读人=%s ｜ 代表性样本 %d 条%s ｜ 判完把结论填回同名 .json 的 items[].verdict / "
         "items[].has_stance，再跑 --apply"
         % (obj["seed"], obj.get("coder") or "（未标）", obj.get("n_items", 0),
            ("＋富集样本 %d 条（低确定度，只查错、不进估计）" % _blk["enrich"]) if _blk.get("enrich") else ""), ""]
    for i, it in enumerate(obj["items"], 1):
        _mk = "【富集·低确定度】" if it.get("block") == "enrich" else ""
        _why = ("　理由：" + "、".join(it.get("uncertainty_reasons") or [])) if it.get("uncertainty_reasons") else ""
        L.append("### %d. %s[%s/%s 赞%s｜作者 %s｜%s] %s"
                 % (i, _mk, it.get("platform"), it.get("type"), it.get("likes"),
                    it.get("author") or "（未署名）", it.get("stratum") or it.get("day_box") or "-",
                    it.get("stance") or "（未分类）"))
        if _why:
            L.append("")
            L.append(_why.strip())
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


def _intercoder(obj, obj_b, args):
    """双人判读一致性（v0.5 新增）：按 (platform, id) 对齐两份判读单，算一致率 / κ / α。

    为什么必须做：单人判读的「误判率」里混着两种东西——**标签体系的错**和**判读人的分歧**。
    κ/α 低说明判读标准本身没对齐，此时拿误判率去校正占比是把判读人的随意性当测量误差，
    正确处置是先仲裁不一致条目、把判读标准写进 codebook，再重跑。
    """
    if not isinstance(obj_b, dict):
        return {"status": "不可用", "reason": "第二份判读单读取失败：%s" % args.coder_b}
    if obj_b.get("kind") != obj.get("kind"):
        return {"status": "不可用",
                "reason": "两份判读单的 --kind 不同（%s vs %s），核不可比" % (obj.get("kind"), obj_b.get("kind"))}
    a = {(str(i.get("platform")), str(i.get("id"))): i for i in (obj.get("items") or [])}
    b = {(str(i.get("platform")), str(i.get("id"))): i for i in (obj_b.get("items") or [])}
    pairs, disagreements = [], []
    for k in sorted(set(a) & set(b)):
        ia, ib = a[k], b[k]
        if obj.get("kind") == "unclassified":
            va, vb = ia.get("has_stance"), ib.get("has_stance")
            if va is None or vb is None:
                continue
            va, vb = bool(va), bool(vb)
        else:
            va = str(ia.get("verdict") or "").strip()
            vb = str(ib.get("verdict") or "").strip()
            if not va or not vb:
                continue
            va = va in ("对", "correct", "true", "是", "1")
            vb = vb in ("对", "correct", "true", "是", "1")
        pairs.append((va, vb))
        if va != vb:
            disagreements.append({"platform": k[0], "id": k[1], "coder_a": va, "coder_b": vb,
                                  "content": (ia.get("content") or "")[:90]})
    res = {"status": "已完成", "kind": obj.get("kind"),
           "coder_a": obj.get("coder") or "（未标）", "coder_b": obj_b.get("coder") or "（未标）",
           "n_items_a": len(a), "n_items_b": len(b), "n_pairs": len(pairs),
           "agreement": None, "cohen_kappa": None, "krippendorff_alpha": None,
           "disagreement_count": len(disagreements), "disagreements": disagreements[:20],
           "arbitration_note": "不一致条目须先仲裁并写进 codebook 的判读标准，再重跑 --make/--apply；"
                               "不得直接取两人的平均值。",
           "note": "κ/α 低说明判读标准本身有分歧：此时用误判率校正占比，等于把判读随意性当成测量误差。"}
    if pairs:
        kd = stats.cohen_kappa(pairs)
        ad = stats.krippendorff_alpha_nominal([[p0, p1] for p0, p1 in pairs])
        res["agreement"] = round(kd["po"] * 100, 1) if kd["po"] is not None else None
        res["cohen_kappa"] = round(kd["kappa"], 3) if isinstance(kd["kappa"], float) else kd["kappa"]
        res["krippendorff_alpha"] = round(ad["alpha"], 3) if isinstance(ad["alpha"], float) else ad["alpha"]
        res["degenerate"] = bool(kd.get("degenerate"))
    res["reading"] = stats.reliability_note(res.get("cohen_kappa"), res.get("krippendorff_alpha"))
    return res


def _cluster_robust(items, is_hit):
    """以**作者**为簇修正的区间：同一作者的多条判读并不独立，逐条独立口径会低估误差。"""
    by = collections.defaultdict(lambda: [0, 0])
    for it in items:
        a = str(it.get("author") or "(未署名)")
        by[a][1] += 1
        if is_hit(it):
            by[a][0] += 1
    cr = stats.clustered_proportion([v[0] for v in by.values()], [v[1] for v in by.values()])
    if not cr:
        return None
    return {"p_pct": cr["p_pct"], "ci95": list(cr["ci95_pct"]), "design_effect": cr["design_effect"],
            "effective_n": cr["effective_n"], "n_clusters": cr["n_clusters"],
            "cluster_sizes": cr["cluster_sizes"], "method": cr["method"],
            "note": "同一作者多条判读并不独立；本区间以作者为簇（PSU）做线性化修正，"
                    "比逐条独立口径更保守，**引用时应优先用它**。"}


def _enrich_analysis(obj, core_items, enrich_items, args):
    """富集块（低确定度）单独分析：它**不代表池的分布**，只回答"机器最容易错在哪一类"。

    与 core 的差异用 Newcombe 独立两样本区间（两块是不同的样本，故用独立口径；
    比 Wald 在小样本下更可靠）。
    """
    kind = obj.get("kind")

    def _rate(items):
        if kind == "unclassified":
            return sum(1 for it in items if it.get("has_stance")), len(items), "含立场表达"
        return sum(1 for it in items
                   if str(it.get("verdict") or "").strip() not in ("对", "correct", "true", "是", "1")), \
            len(items), "误判"

    k_e, n_e, what = _rate(enrich_items)
    k_c, n_c, _w2 = _rate(core_items)
    reasons = collections.Counter()
    for it in enrich_items:
        for x in it.get("uncertainty_reasons") or []:
            reasons[x] += 1
    out = {
        # 与"未判读完成"形态保持同一个字段名，消费者不必分两种形状判断
        "status": "已完成",
        "what": what,
        "n_enrich": n_e,
        "n_core": n_c,
        "enrich_count": k_e,
        "enrich_rate_pct": round(k_e / n_e * 100, 1) if n_e else None,
        "enrich_rate_ci95": list(stats.wilson_pct(k_e, n_e)) if n_e else None,
        "core_rate_pct": round(k_c / n_c * 100, 1) if n_c else None,
        "core_rate_ci95": list(stats.wilson_pct(k_c, n_c)) if n_c else None,
        "enrich_authors": len({str(it.get("author") or "(未署名)") for it in enrich_items}),
        "enrich_reasons": dict(reasons.most_common()),
        "enrich_cluster_robust": _cluster_robust(enrich_items, lambda it: (
            it.get("has_stance") if kind == "unclassified"
            else str(it.get("verdict") or "").strip() not in ("对", "correct", "true", "是", "1"))),
        "note": "富集块是**低确定度记录**的过抽样，**不代表池的分布**，不得用来估计占比或误判率；"
                "它的用途是回答「机器最容易在哪一类上出错」——"
                "若富集块的错误率显著高于核心样本，说明核心样本的误判率是**低估**，"
                "须在结论里写明这个方向（而不是把两者平均）。",
    }
    if n_e and n_c:
        ci = stats.newcombe_diff_ci(k_e, n_e, k_c, n_c)
        out["diff_pp"] = round((k_e / n_e - k_c / n_c) * 100, 1)
        out["diff_ci95_pp"] = [round(ci[0] * 100, 1), round(ci[1] * 100, 1)]
        out["verdict"] = stats.diff_verdict(ci)
    return out


def render_html(obj, title_suffix=""):
    """自包含的离线判读单（E5.4）：点选录入 → 导出与判读单**同结构**的 JSON → 再跑 --apply。

    设计约束（刻意为之）：
      · 单文件、内联 CSS/JS、**不联网、不引任何外部资源**（无 CDN、无字体、无框架）；
      · JSON 仍是唯一权威：HTML 只是录入界面，导出的文件结构与 --make 生成的完全一致；
      · 导出时带上 `exported_by` 与 `n_items` 自检，`--apply` 会核对条目数是否与判读单一致。
    """
    payload = json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")
    html = _HTML_TEMPLATE
    html = html.replace("__TITLE__", "人工抽检判读单（%s 核）%s" % (obj.get("kind", ""), title_suffix))
    html = html.replace("__KIND__", str(obj.get("kind", "")))
    html = html.replace("__SEED__", str(obj.get("seed", "")))
    html = html.replace("__CODER__", str(obj.get("coder") or "（未标）"))
    html = html.replace("__NCORE__", str((obj.get("blocks") or {}).get("core", obj.get("n_items", 0))))
    html = html.replace("__NENRICH__", str((obj.get("blocks") or {}).get("enrich", 0)))
    html = html.replace("__OUTNAME__", os.path.basename(str(obj.get("_form_path") or "spotcheck.json")))
    html = html.replace("__PAYLOAD__", payload)
    return html


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
 :root{--bg:#f6f7f9;--card:#fff;--line:#dfe3e8;--ink:#1f2430;--dim:#66707f;--ok:#1b7f4b;--bad:#b3261e;--acc:#2a5fd6}
 *{box-sizing:border-box}
 body{margin:0;font:15px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink)}
 header{position:sticky;top:0;background:#fff;border-bottom:1px solid var(--line);padding:12px 18px;z-index:9}
 h1{font-size:17px;margin:0 0 4px}
 .meta{color:var(--dim);font-size:13px}
 .bar{margin-top:8px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
 .progress{flex:1;height:8px;background:#eceff3;border-radius:4px;overflow:hidden;min-width:160px}
 .progress > i{display:block;height:100%;width:0;background:var(--acc);transition:width .15s}
 main{padding:14px 18px 96px;max-width:1000px;margin:0 auto}
 .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:12px}
 .card.enrich{border-left:5px solid #d9a441}
 .card.core{border-left:5px solid #8fb3f0}
 .head{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;font-size:13px;color:var(--dim)}
 .content{margin:10px 0;white-space:pre-wrap;word-break:break-word}
 .tags span{display:inline-block;background:#eef2f8;color:#33415c;border-radius:4px;padding:1px 7px;margin:0 6px 4px 0;font-size:12px}
 .tags span.warn{background:#fdf1e0;color:#8a5a10}
 .ask{color:var(--dim);font-size:13px;margin-bottom:8px}
 .btns{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
 button{font:inherit;padding:6px 14px;border:1px solid var(--line);background:#fff;border-radius:8px;cursor:pointer}
 button:hover{border-color:var(--acc)}
 button.on[data-v="1"],button.on[data-v="0"]{color:#fff;border-color:transparent}
 button.on[data-v="1"]{background:var(--ok)}
 button.on[data-v="0"]{background:var(--bad)}
 input[type=text]{font:inherit;padding:5px 9px;border:1px solid var(--line);border-radius:8px;min-width:180px}
 input.verdict{min-width:140px}
 footer{position:fixed;left:0;right:0;bottom:0;background:#fff;border-top:1px solid var(--line);padding:10px 18px;display:flex;gap:14px;align-items:center;flex-wrap:wrap;z-index:9}
 footer .msg{color:var(--dim);font-size:13px}
 .done-mask{opacity:.62}
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <div class="meta">seed=__SEED__ ｜ 判读人=__CODER__ ｜ 代表性样本 __NCORE__ 条 ｜ 富集样本 __NENRICH__ 条（只查错，不进估计）</div>
  <div class="bar">
    <div class="progress"><i id="pg"></i></div>
    <div class="meta">已完成 <b id="done">0</b> / <b id="total">0</b></div>
    <div class="meta" id="strict">核心样本（代表性）必填；富集样本可选（只查错，不进估计）</div>
  </div>
</header>
<main id="list"></main>
<footer>
  <button id="export">导出 JSON（判读结果）</button>
  <span class="msg" id="msg">导出后用 <code>spotcheck.py --apply --out &lt;导出的文件&gt;</code> 计算，并把文件替换掉原判读单。</span>
</footer>
<script>
const FORM = __PAYLOAD__;
const OUTNAME = "__OUTNAME__";
const KIND = "__KIND__";
const list = document.getElementById("list");
const pg = document.getElementById("pg"), doneEl = document.getElementById("done"), totalEl = document.getElementById("total");
let done = 0;

function el(tag, cls, text){const e=document.createElement(tag); if(cls) e.className=cls; if(text!==undefined) e.textContent=text; return e;}

FORM.items.forEach(function(it, idx){
  const c = el("div", "card " + (it.block === "enrich" ? "enrich" : "core"));
  const head = el("div", "head");
  head.appendChild(el("span", null, "#" + (idx+1) + "　" + (it.platform||"") + "/" + (it.type||"") +
      "　赞" + (it.likes===undefined?"-":it.likes) + "　作者 " + (it.author || "（未署名）")));
  head.appendChild(el("span", null, "分层 " + (it.stratum || it.day_box || "-") +
      "　" + (it.block === "enrich" ? "富集（只查错）" : "代表性")));
  c.appendChild(head);
  c.appendChild(el("div", "content", it.content || ""));
  const tags = el("div", "tags");
  (it.uncertainty_reasons || []).forEach(function(r){ tags.appendChild(el("span", "warn", r)); });
  if (it.matched && it.matched.length) tags.appendChild(el("span", null, "规则命中：" + it.matched.join(" / ")));
  if (it.note) tags.appendChild(el("span", null, it.note));
  c.appendChild(tags);
  c.appendChild(el("div", "ask", it._q || ""));

  const btns = el("div", "btns");
  function mark(btn, value, group){
    if (it.block === "enrich") {} // 富集条目同样要判，只是不参与估计
    if (KIND === "unclassified") {
      it.has_stance = (value === 1);
    } else {
      it.verdict = (value === 1) ? "对" : "错";
    }
    group.forEach(function(b){ b.classList.remove("on"); });
    btn.classList.add("on");
    refresh(c, it);
    count();
  }
  if (KIND === "unclassified") {
    [["含立场表达", 1], ["不含立场表达", 0]].forEach(function(pair){
      const b = el("button", null, pair[0]); b.dataset.v = pair[1];
      b.onclick = function(){ mark(b, pair[1], btns.children); };
      btns.appendChild(b);
    });
    const st = el("input", "verdict"); st.type = "text"; st.placeholder = "立场名（含立场时填）";
    st.value = it.stance || ""; st.oninput = function(){ it.stance = st.value; count(); };
    it._stanceInput = st;
    btns.appendChild(st);
  } else {
    [["对", 1], ["错", 0]].forEach(function(pair){
      const b = el("button", null, pair[0]); b.dataset.v = pair[1];
      b.onclick = function(){ mark(b, pair[1], btns.children); };
      btns.appendChild(b);
    });
    const nt = el("input", "verdict"); nt.type = "text"; nt.placeholder = "备注（可选）";
    nt.value = it.note_text || ""; nt.oninput = function(){ it.note_text = nt.value; };
    btns.appendChild(nt);
  }
  c.appendChild(btns);
  it._card = c;
  list.appendChild(c);
  refresh(c, it);
});

function isDone(it){
  if (KIND === "unclassified") return (it.has_stance === true || it.has_stance === false);
  return !!(it.verdict && String(it.verdict).trim());
}
function refresh(card, it){
  if (isDone(it)) card.classList.add("done-mask"); else card.classList.remove("done-mask");
  if (KIND === "unclassified" && it._stanceInput) it._stanceInput.disabled = !it.has_stance;
}
function count(){
  done = FORM.items.filter(isDone).length;
  doneEl.textContent = done; totalEl.textContent = FORM.items.length;
  pg.style.width = (FORM.items.length ? Math.round(done*100/FORM.items.length) : 0) + "%";
}
count();

document.getElementById("export").onclick = function(){
  const coreMiss = FORM.items.map(function(it, i){ return (it.block === "enrich" || isDone(it)) ? null : (i+1); })
                             .filter(function(x){ return x!==null; });
  const enrichMiss = FORM.items.filter(function(it){ return it.block === "enrich" && !isDone(it); }).length;
  const msg = document.getElementById("msg");
  if (coreMiss.length) {
    msg.textContent = "代表性样本还有 " + coreMiss.length + " 条未判（如 #" + coreMiss.slice(0,8).join(", #") +
                      "）：脚本不产出部分结果，请补齐后再导出。";
    msg.style.color = "var(--bad)";
    return;
  }
  const out = JSON.parse(JSON.stringify(FORM));
  out.items.forEach(function(it){ delete it._card; delete it._stanceInput; });
  out.exported_by = "spotcheck-html/v1";
  out.exported_at_items = out.items.length;
  const blob = new Blob([JSON.stringify(out, null, 2)], {type:"application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = OUTNAME;
  document.body.appendChild(a); a.click(); a.remove();
  msg.textContent = "已导出 " + OUTNAME + "（" + out.items.length + " 条）" +
      (enrichMiss ? "；富集样本还有 " + enrichMiss + " 条未判（不阻塞 --apply，只是不出富集对照）" : "") +
      "。用 --apply 读取该文件即可。";
  msg.style.color = "var(--ok)";
};
</script>
</body>
</html>
"""


def cmd_apply(args):
    obj = _load_json(args.out)
    if not obj:
        print("[spotcheck] 找不到判读单：%s（先跑 --make）" % args.out)
        return 2
    items = obj.get("items") or []
    # E5.4：若是 HTML 判读单导出的文件，核对条目数（防止用别的判读单覆盖）
    if obj.get("exported_by") and obj.get("exported_at_items") not in (None, len(items)):
        print("[spotcheck] ⚠ 该文件由离线判读单导出（%s），但条目数 %s ≠ 判读单条目数 %d："
              "请确认没有拿错文件。" % (obj.get("exported_by"), obj.get("exported_at_items"), len(items)))
    # 块拆分（v0.6）：只有 core（代表性样本）参与估计；enrich 单独报，并明确标注非代表性。
    # 旧判读单没有 block 字段 → 全部视为 core，行为与 v0.5 完全一致（向后兼容）。
    core_items = [it for it in items if (it.get("block") or "core") == "core"]
    enrich_items = [it for it in items if it.get("block") == "enrich"]

    def _filled(it):
        if obj["kind"] == "unclassified":
            return it.get("has_stance") is not None
        return bool(str(it.get("verdict") or "").strip())

    # P4：完整性检查只对 **core** 强制，富集块未判完不阻塞。
    # 为什么必须改：富集块自己声明"不参与估计"，但默认 --enrich-n 20 会让三类核多出 60 条必判项，
    # 用户为了拿到代表性估计就得多判两倍条目——在一个"必须抽检才能引用"的工作流里，
    # 这恰恰会把用户逼到干脆跳过抽检，与脚本初衷相反。
    missing = [i for i, it in enumerate(core_items, 1) if not _filled(it)]
    if missing:
        print("[spotcheck] 判读未完成（代表性样本）：core #%s 仍为空（缺 %d/%d）——"
              "脚本不产出部分结果，请补齐后重跑 --apply" % (missing[:8], len(missing), len(core_items)))
        return 2
    enrich_missing = [i for i, it in enumerate(enrich_items, 1) if not _filled(it)]

    core = core_items or items
    result = {"kind": obj["kind"], "seed": obj["seed"], "n_items": len(core),
              "blocks": {"core": len(core), "enrich": len(enrich_items),
                         "note": "core=代表性样本（参与估计）｜enrich=低确定度富集（只查错，不参与估计；"
                                 "未判完不阻塞 --apply）"}}
    if enrich_items and not enrich_missing:
        result["enrich"] = _enrich_analysis(obj, core, enrich_items, args)
    elif enrich_items:
        result["enrich"] = {
            "status": "未判读完成", "n_enrich": len(enrich_items), "n_incomplete": len(enrich_missing),
            "note": "富集块只用于查错、不参与任何估计，故**不阻塞 --apply**；本次不出富集对照。"
                    "要得到「机器最容易错在哪一类」的对照，请补齐这 %d 条后重跑 --apply。"
                    % len(enrich_missing)}
    if obj["kind"] in ("stance", "rule"):
        per = collections.defaultdict(lambda: {"n": 0, "wrong": 0, "items": []})
        for it in core:
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
        err_max = float(args.error_rate_max)
        # 每个立场的误判率同时给 Wilson 区间：n=10 时 1 条误判与 3 条误判的区间是重叠的，
        # 只看点估计会把「样本太少」读成「这个立场很准」。
        result["per_stance"] = {k: {"n": v["n"], "wrong": v["wrong"],
                                    "error_rate": round(v["wrong"] / v["n"] * 100, 1),
                                    "error_rate_ci95": list(stats.wilson_pct(v["wrong"], v["n"])),
                                    "examples": v["items"][:3],
                                    "usable_as_main": v["wrong"] / v["n"] * 100 <= err_max}
                                for k, v in sorted(per.items())}
        result["overall_error_rate"] = round(w_all / n_all * 100, 1) if n_all else 0
        result["overall_error_rate_ci95"] = list(stats.wilson_pct(w_all, n_all)) if n_all else [0.0, 0.0]
        result["error_rate_max"] = err_max
        result["unreliable_stances"] = [k for k, v in result["per_stance"].items()
                                        if not v["usable_as_main"]]
        _cr = _cluster_robust(core, lambda it: str(it.get("verdict") or "").strip() not in
                              ("对", "correct", "true", "是", "1"))
        if _cr:
            result["overall_error_rate_clustered"] = _cr
    else:
        n = len(core)
        with_stance = [it for it in core if it.get("has_stance")]
        by = collections.Counter((it.get("stance") or "（未填）").strip() for it in with_stance)
        p = len(with_stance) / n
        se = (p * (1 - p) / n) ** 0.5 if n else 0
        pool = next((x.get("pool") for x in (obj.get("sampling_plan") or []) if x.get("pool")), 0)
        result["with_stance"] = len(with_stance)
        result["without_stance"] = n - len(with_stance)
        result["p_with_stance"] = round(p * 100, 1)
        result["ci95_pp"] = round(1.96 * se * 100, 1)                     # 正态近似半宽（旧字段，保留可追溯）
        result["p_with_stance_ci95"] = list(stats.wilson_pct(len(with_stance), n))
        result["estimated_with_stance_in_pool"] = int(round(pool * p))
        # v0.5：区间改用 Wilson（正态近似在小样本/极端比例下会越界或过窄）；
        # 另给**后分层加权**估计与设计效应——抽样是按平台×点赞档分层的，
        # 简单随机口径会同时错估点值与误差，两者差异必须披露而不是取其一。
        result["estimated_total_range"] = stats.scale_interval(
            stats.wilson_interval(len(with_stance), n), pool)
        result["stance_mix_in_sample"] = dict(by.most_common())
        result["pool_size"] = pool
        _cr = _cluster_robust(core, lambda it: bool(it.get("has_stance")))
        if _cr:
            result["p_with_stance_clustered"] = _cr
        _plan = (obj.get("sampling_plan") or [{}])[0]
        strata_plan = _plan.get("strata") or []
        k_by = collections.Counter()
        for it in with_stance:
            # 用条目自带的 stratum（平台×点赞档×时间箱），与 --make 的分层键逐字一致；
            # 只有老判读单没这个字段时才退回旧写法。
            key = it.get("stratum") or ("%s/%s" % (it.get("platform"), _band(it)))
            k_by[key] += 1
        strat = stats.stratified_proportion([
            {"name": s.get("stratum"), "N": s.get("size"), "n": s.get("sampled"),
             "k": k_by.get(s.get("stratum"), 0)} for s in strata_plan])
        # 防呆：若判读单的分层标签与计划完全对不上（老判读单缺 stratum 字段、或分层已改），
        # 分层估计会全取到 k=0 而渲染出"0.0%"这种伪测量值——宁可不出，也不出错数。
        if strata_plan and not (set(s.get("stratum") for s in strata_plan) & set(k_by)):
            strat = None
            result["stratified_note"] = ("分层口径与判读单不匹配（条目缺 stratum 字段或分层维度已变）："
                                         "本次不出分层加权估计；简单随机口径与核心样本区间仍然可用。"
                                         "如需分层估计，请重新 --make 生成判读单。")
        if strat and pool:
            result["stratified"] = {
                "method": strat["method"],
                "p_with_stance": strat["p_pct"],
                "p_with_stance_ci95": list(strat["ci95_pct"]),
                "estimated_in_pool": int(round(pool * strat["p"])),
                "estimated_in_pool_range": stats.scale_interval(strat["ci95"], pool),
                "design_effect": strat["design_effect"],
                "effective_n": strat["effective_n"],
                "n_strata": strat["n_strata"],
                "unsampled_N": strat["unsampled_N"],
                "note": "后分层加权估计（层大小取未识别池的实际分布）。设计效应 <1 表示分层抽样比同规模"
                        "简单随机更省，>1 则更费，都须披露。unsampled_N>0 表示有些层没抽到——"
                        "那些层的条数计入了权重但不贡献比例，是隐性偏倚的来源。",
            }
        result["inference"] = {
            "method": "wilson (single proportion) + post-stratification",
            "note": "p_with_stance / estimated_with_stance_in_pool 是简单随机口径（旧字段，保留可追溯）；"
                    "stratified 字段是分层加权口径，**结论请优先引用它**，两者的差即分层带来的修正。"
                    "未分类段估计还须与 opinion.json 的占比并排披露，不得单独出现。",
        }
        result["thresholds"] = {"coverage_target": args.coverage_target,
                                "coverage_target_source": args._coverage_target_source,
                                "config_source": args._cfg_source}
    result["status"] = "已完成"
    result["coder"] = obj.get("coder") or ""
    result["pipeline"] = obj.get("pipeline") or _pipeline()    # 双人判读一致性（可选）：只判读完两份才计算，缺失时明确 null（不得默认当作判读可靠）
    if args.coder_b:
        result["intercoder"] = _intercoder(obj, _load_json(args.coder_b), args)
    else:
        result["intercoder"] = {"status": "未提供第二判读人",
                                "note": "单判读人的误判率没有信度支撑。若结论重要，请让第二人独立判读同一批样本"
                                        "（同一 seed 生成另一份判读单），再用 --coder-b 计算一致率/κ/α。"}
    # 先清掉上一轮 --apply 留下的结果键，再写入本轮结果：
    # 否则同一份判读单重复 --apply（换参数、换快照）会留下过期字段，
    # 例如老格式判读单重跑后仍挂着上一轮的 enrich/stratified——那种"幽灵结论"比缺字段更危险。
    for _k in ("n_items", "blocks", "per_stance", "overall_error_rate", "overall_error_rate_ci95",
               "error_rate_max", "unreliable_stances", "overall_error_rate_clustered",
               "with_stance", "without_stance", "p_with_stance", "ci95_pp", "p_with_stance_ci95",
               "p_with_stance_clustered", "estimated_with_stance_in_pool", "estimated_total_range",
               "stance_mix_in_sample", "pool_size", "stratified", "inference", "thresholds",
               "enrich", "intercoder", "coder", "pipeline"):
        obj.pop(_k, None)
    obj.update(result)
    obj["status"] = "已完成"
    # 刷新状态摘要（v0.7）：让 --status 不读全文件也能给出结论数字
    _sm = obj.get("summary") or {}
    _sm.update({"status": "已完成", "kind": obj.get("kind"),
                "n_core": (obj.get("blocks") or {}).get("core"),
                "n_enrich": (obj.get("blocks") or {}).get("enrich")})
    for _k, _src in (("overall_error_rate", "overall_error_rate"),
                     ("unreliable_stances", "unreliable_stances"),
                     ("p_with_stance", "p_with_stance"),
                     ("p_with_stance_ci95", "p_with_stance_ci95"),
                     ("estimated_with_stance_in_pool", "estimated_with_stance_in_pool"),
                     ("stratified_p_with_stance", None)):
        if _k == "stratified_p_with_stance":
            _v = ((result.get("stratified") or {}) or {}).get("p_with_stance")
            if _v is not None:
                _sm["stratified_p_with_stance"] = _v
            continue
        if result.get(_src) is not None:
            _sm[_k] = result[_src]
    if (result.get("enrich") or {}).get("n_enrich"):
        _sm["enrich_diff_pp"] = result["enrich"].get("diff_pp")
        _sm["enrich_verdict"] = result["enrich"].get("verdict")
    if (result.get("intercoder") or {}).get("status") == "已完成":
        _sm["cohen_kappa"] = result["intercoder"].get("cohen_kappa")
        _sm["krippendorff_alpha"] = result["intercoder"].get("krippendorff_alpha")
    obj["summary"] = _sm
    _save_json(args.out, obj)
    try:
        print("[spotcheck] 工件 %.1f KB；看状态用 `python spotcheck.py --status --out %s`"
              "（勿整体读取 JSON）" % (os.path.getsize(args.out) / 1024.0, os.path.basename(args.out)))
    except OSError:
        pass

    # 回写 opinion.json：抽检结论必须落在聚类工件上，报告与门禁都读它
    op_path = obj.get("opinion")
    opinion = _load_json(op_path)
    if opinion:
        sc = opinion.setdefault("spotcheck", {})
        sc[obj["kind"]] = result
        sc["_note"] = ("人工抽检结论（spotcheck.py --apply 写入）。纪律：未抽检的标签不得作为结论依据；"
                       "误判率 >%.0f%% 的立场不得出现在主立场/主流/压倒性表述里；"
                       "未分类段的估计必须与占比并列披露；未提供第二判读人时不得声称判读可靠。"
                       % float(args.error_rate_max))
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
    _erm = float(obj.get("error_rate_max") or 30)
    if obj["kind"] in ("stance", "rule"):
        L.append("**抽检口径**：seed=%s，判读人=%s，共判读 %d 条；每立场按平台×点赞档配额抽样。"
                 % (obj["seed"], obj.get("coder") or "（未标）", obj["n_items"]))
        L.append("")
        L.append("| 立场 | 抽样 | 误判 | 误判率 | 95%CI | 可否作为主结论 |")
        L.append("| --- | --- | --- | --- | --- | --- |")
        for k, v in (obj.get("per_stance") or {}).items():
            ci = v.get("error_rate_ci95")
            L.append("| %s | %d | %d | %.1f%% | %s | %s |"
                     % (k, v["n"], v["wrong"], v["error_rate"], stats.fmt_ci(ci),
                        "可" if v["usable_as_main"] else "**不可**（>%.0f%%）" % _erm))
        L.append("")
        if obj.get("unreliable_stances"):
            L.append("⚠ **以下立场误判率 >%.0f%%，不得作为主结论呈现**：%s。"
                     % (_erm, "、".join(obj["unreliable_stances"])))
            L.append("")
    else:
        L.append("**未分类段抽样**：从未识别层 %d 条（= opinion.json 的 residual，与报告口径行同源）中按"
                 "平台×点赞档抽 %d 条人工判读，其中 **%d 条含立场表达（%.1f%%，95%%CI %s%%）**，"
                 "%d 条不含（纯反应/玩笑/信息）。"
                 % (obj.get("pool_size", 0), obj["n_items"], obj["with_stance"], obj["p_with_stance"],
                    stats.fmt_ci(obj.get("p_with_stance_ci95")), obj["without_stance"]))
        L.append("")
        L.append("简单随机口径回推：**未识别层约 %d 条含立场表达**（95%% 置信区间 %s 条）。"
                 % (obj["estimated_with_stance_in_pool"], "%d–%d" % tuple(obj["estimated_total_range"])))
        L.append("")
        st = obj.get("stratified")
        if st:
            L.append("分层加权口径（**推荐引用**）：**约 %d 条含立场表达（%.1f%%，95%%CI %s%%）**，"
                     "对应 %s 条；设计效应 %.2f，有效样本量 %s，层数 %d，未抽到层的条数 %d。"
                     % (st["estimated_in_pool"], st["p_with_stance"],
                        "–".join("%.1f" % x for x in st["p_with_stance_ci95"]),
                        "%d–%d" % tuple(st["estimated_in_pool_range"]),
                        st["design_effect"] if st["design_effect"] is not None else -1,
                        st["effective_n"], st["n_strata"], st["unsampled_N"]))
            L.append("")
        L.append("即全量语料里还有约 %d 条立场表达未进入占比——故本章占比应读作"
                 "「已识别部分内的结构」，其绝对水平被系统性低估。"
                 % (st["estimated_in_pool"] if st else obj["estimated_with_stance_in_pool"]))
        L.append("")
        L.append("样本内立场构成：%s" % "、".join("%s %d 条" % (k, v) for k, v in
                                                  (obj.get("stance_mix_in_sample") or {}).items()))
        L.append("")
    ic = obj.get("intercoder") or {}
    if ic.get("status") == "已完成":
        L.append("**编码信度**：已完成")
        L.append("")
        L.append("- 判读人：A=%s / B=%s；可对齐条目 %d 条" % (ic.get("coder_a"), ic.get("coder_b"), ic.get("n_pairs", 0)))
        L.append("- 一致率 %.1f%% ｜ Cohen κ %s ｜ Krippendorff α %s"
                 % (ic.get("agreement") or 0, ic.get("cohen_kappa"), ic.get("krippendorff_alpha")))
        L.append("- 判读：%s" % ic.get("reading"))
        if ic.get("disagreement_count"):
            L.append("- 不一致 %d 条，须先仲裁并写进 codebook 的判读标准，再重跑（不得取两人平均）"
                     % ic["disagreement_count"])
    else:
        L.append("**编码信度**：%s" % (ic.get("status") or "未提供第二判读人"))
        L.append("")
        L.append("- %s" % (ic.get("note") or ic.get("reason") or ""))
    L.append("")
    # 富集块（低确定度）与核心样本的对照：回答"机器最容易错在哪一类"
    en = obj.get("enrich") or {}
    if en.get("status") == "未判读完成":
        L.append("**分歧优先的富集样本（非代表性，只查错）**：未判读完成（%d/%d 条仍为空），"
                 "本次不出该对照；代表性估计不受影响。"
                 % (en.get("n_incomplete", 0), en.get("n_enrich", 0)))
        L.append("")
    elif en.get("n_enrich"):
        L.append("**分歧优先的富集样本（非代表性，只查错）**")
        L.append("")
        L.append("- 富集 %d 条（作者 %d 位）：%s率 %.1f%%（95%%CI %s%%）；核心样本同指标 %.1f%%（95%%CI %s%%）"
                 % (en["n_enrich"], en.get("enrich_authors", 0), en.get("what", ""),
                    en.get("enrich_rate_pct") or 0, stats.fmt_ci(en.get("enrich_rate_ci95")),
                    en.get("core_rate_pct") or 0, stats.fmt_ci(en.get("core_rate_ci95"))))
        if en.get("diff_pp") is not None:
            L.append("- 差值 %+.1f 个百分点（95%%CI %.1f–%.1f）：%s"
                     % (en["diff_pp"], en["diff_ci95_pp"][0], en["diff_ci95_pp"][1], en["verdict"]))
        if en.get("enrich_reasons"):
            L.append("- 富集理由分布：%s" % "、".join("%s×%d" % (k, v) for k, v in en["enrich_reasons"].items()))
        L.append("- %s" % en.get("note", ""))
        L.append("")
    cr = obj.get("overall_error_rate_clustered") or obj.get("p_with_stance_clustered")
    if cr:
        L.append("**以作者为簇的修正口径（引用优先）**：%.1f%%（95%%CI %s%%，deff %s，有效样本量 %s，簇 %d）"
                 % (cr["p_pct"], "–".join("%.1f" % z for z in cr["ci95"]), cr["design_effect"],
                    cr["effective_n"], cr["n_clusters"]))
        L.append("")
    print("\n".join(L))
    out = args.md or (args.out.rsplit(".", 1)[0] + "_结论.md")
    with io.open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("[spotcheck] 结论片段 → %s" % out)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="",
                    choices=["stance", "rule", "unclassified", ""],
                    help="抽检核：stance / rule / unclassified（--status 模式可省略）")
    ap.add_argument("--in", dest="src", default="", help="分析语料（data.relevant.jsonl）")
    ap.add_argument("--opinion", default="", help="opinion.json（读立场分布；--apply 时回写抽检结论）")
    ap.add_argument("--stances", default="", help="stances.json（重算标签用；缺省取 --opinion 同目录）")
    ap.add_argument("--rules", default="", help="opinion_rules.json（rule 核必须传）")
    ap.add_argument("--institutional", default="",
                    help="机构识别规则 json（与 opinion.py 同名同义）。聚类时若用了自定义规则，"
                         "这里必须同样传入，否则抽检池会与 opinion.residual 不同源（v0.5 D13）")
    ap.add_argument("--llm-labels", dest="llm_labels", default="",
                    help="外部 LLM 标注（可选，与 opinion.py 同格式）：仅用于确定度评分——"
                         "LLM 与词典层打架的记录会被优先抽进富集块。脚本不联网")
    ap.add_argument("--llm-labels-b", dest="llm_labels_b", default="",
                    help="第二个 LLM 模型标注（可选）：两模型互相不一致的记录同样优先进富集块")
    ap.add_argument("--llm-conf-min", dest="llm_conf_min", type=float, default=0.7,
                    help="LLM 置信度下限（默认 0.7）：低于它的记录进富集块优先队列")
    ap.add_argument("--html", action="store_true",
                    help="另生成自包含的离线判读单 spotcheck.html（点选式录入，导出 JSON 后仍用 --apply）")
    ap.add_argument("--enrich-n", type=int, default=20,
                    help="富集块条数上限（默认 20）：低确定度记录（命中词少/疑似否定/层间不一致/"
                         "同源文本/过短）优先抽，**只用于查错，不进估计**；0 = 关闭富集块")
    ap.add_argument("--no-time-strata", action="store_true",
                    help="关闭时间分层（默认按日期等分最多 3 箱参与分层，防止整段时间被漏抽）")
    ap.add_argument("--dup-threshold", type=int, default=3,
                    help="同源文本判定：64 位 SimHash 汉明距离阈值（默认 3），用于确定度评分")
    ap.add_argument("--allow-pool-mismatch", action="store_true",
                    help="允许未分类池 ≠ opinion.residual 并降级为披露（默认硬失败 exit 2）")
    ap.add_argument("--coder", default="", help="判读人标识（如 A/B），写入判读单，供双人信度核对")
    ap.add_argument("--coder-b", dest="coder_b", default="",
                    help="第二判读人的判读单 json；传入后 --apply 会额外计算一致率/Cohen κ/Krippendorff α，"
                         "并把不一致条目列成仲裁队列")
    ap.add_argument("--out", default="", help="data/{event_id}/spotcheck.json")
    ap.add_argument("--status", action="store_true",
                    help="只打印一屏状态（≤25 行）后退出：**看判读单的推荐方式**，避免整体读取大 JSON")
    ap.add_argument("--pending", action="store_true",
                    help="配合 --status：另列待判读条目（ID / 块 / 确定度理由）")
    ap.add_argument("--n", type=int, default=None,
                    help="每立场抽样条数（缺省取 config.json 的 spotcheck_n，再缺省 10）")
    ap.add_argument("--seed", type=int, default=None,
                    help="抽样种子（缺省取 config.json 的 seed，再缺省 2026；固定值保证可复现）")
    ap.add_argument("--error-rate-max", type=float, default=None,
                    help="误判率上限（百分点；缺省取 config.json 的 error_rate_max，再缺省 30）："
                         "超过则该立场不得作为主结论")
    ap.add_argument("--coverage-target", type=float, default=None,
                    help="覆盖率目标（百分点；缺省取 config.json 的 coverage_target，再缺省 70），仅用于措辞告警")
    ap.add_argument("--make", action="store_true", help="生成判读单")
    ap.add_argument("--apply", action="store_true", help="读回判读结果并计算")
    ap.add_argument("--md", default="", help="--report 输出路径")
    ap.add_argument("--report", action="store_true", help="渲染结论 markdown 片段")
    args = ap.parse_args()
    # --status / --pending：只读判读单的摘要模式，不跑抽样也不跑计算
    if args.status or args.pending:
        if not args.out:
            ap.error("--status/--pending 需要用 --out 指定判读单路径")
        return status(args.out, pending=bool(args.pending))
    if not args.kind or not args.src or not args.out:
        ap.error("--kind / --in / --out 为必需参数（或用 --status --out <spotcheck.json> 只看状态）")
    _cfg = load_config()
    args.n, args._n_source = pick(args.n, _cfg, "spotcheck_n")
    args.seed, args._seed_source = pick(args.seed, _cfg, "seed")
    args.error_rate_max, _erm_src = pick(args.error_rate_max, _cfg, "error_rate_max")
    args.coverage_target, args._coverage_target_source = pick(args.coverage_target, _cfg, "coverage_target")
    args._cfg_source = _cfg.get("_source")
    args.n = int(args.n)
    args.seed = int(args.seed)
    print("[spotcheck] 阈值：n=%d(%s) seed=%d(%s) 误判率上限=%.0f%%(%s) config=%s"
          % (args.n, args._n_source, args.seed, args._seed_source, float(args.error_rate_max), _erm_src,
             args._cfg_source))
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
