# -*- coding: utf-8 -*-
"""bai-bao-dai 舆论立场聚类（v0.3，四层口径）。

读 normalized JSONL，按事件定制的立场词典打标（优先 --stances 文件，
缺省读取 config.json 的 stance_keywords；两者皆空时报错退出）：

四层口径（v0.3 起为默认，可用 --no-split 退回旧单层行为）：
  A 个人表达样本   —— 命中立场词典的个人账号帖文/评论 + 知乎回答，**立场占比的分母**
  B 机构/媒体帖    —— 机构账号帖，或含报道用语且带话题标签者；信息供给方，不进分母
  C 未归类残差     —— 未命中词典者（再分机构帖 / 个人 UGC），并输出诊断
  D 治理剔除噪声   —— 由上游语料治理剔除，仅计数（需 --total-raw 传入原始条数）

为什么要把 B/C 摘出去：机构帖是信息供给方而非舆论主体，把「报道了某立场」当成「持某立场」，
会让高赞代表被媒体转述帖占满；把「未归类」当成一个立场画进占比，则是把方法缺陷当数据结论。

附带产出：
- 样本可溯源：每条 top_sample 带 platform/id/url/ref_url（评论无永久链接时回指其父帖）
- 残差诊断 other→residual.diagnostics：高频 n-gram、与立场词表的差集、按点赞**中位段**抽样
  （不用高赞——高赞多为媒体帖，代表性最差）
- 词典质检 dictionary_quality：各关键词文档频率，df=0 即零命中
- 焦点转移 stance_timeline：**仅统计 A 层**

用法：
  python opinion.py --in data/demo/normalized/data.jsonl --out data/demo/opinion.json \
      --event "事件关键词" --stances data/demo/stances.json
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
OTHER = "未识别"

# 机构/媒体识别（启发式，识别下界：平台常对账号名做匿名化，如「潇***报」）
# v0.4：删掉「报道用语 + 话题标签」这条规则——话题标签网友也用得极多，
#       该规则会把普通网友长帖误判为机构帖、从分母里拿走真实舆情。宁可少判（保守），不可误判（污染分母）。
DEFAULT_INSTITUTIONAL = {
    "name_patterns": ["报", "新闻", "网", "日报", "晚报", "时报", "观察", "频道", "媒体", "广电",
                      "广播", "晨报", "快报", "都市", "电视台", "共青团", "妇联", "发布", "官方",
                      "资讯", "客户端", "融媒", "闻", "频"],
    "repost_patterns": [],
    "note": "仅按账号名机构特征词判定（启发式下界）。空 repost_patterns 即不启用正文规则（v0.4）。",
}

_STOP = set("""
一个 这个 那个 什么 就是 这样 我们 他们 自己 没有 不能 可以 已经 因为 所以 但是 如果 还是
真的 这些 那些 时候 现在 知道 觉得 应该 可能 事情 孩子 小孩 男童 女童 女子 女士 男孩 女生
一条 一下 一些 这种 那种 一直 两个 三个 这么 那么 不是 不要 不会 出来 起来 上来 下来 过来
哈哈 笑哭 捂脸 微笑 失望 哭惹 生气 石化 图片 评论 转发 视频 微博
""".split())


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
    """加载立场词典：优先 --stances 指定的文件；否则用 config.json 的 stance_keywords。"""
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


def load_institutional(path=""):
    """加载机构识别规则；缺省用内置 DEFAULT_INSTITUTIONAL。"""
    cfg = dict(DEFAULT_INSTITUTIONAL)
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                user = json.load(f)
            for k in ("name_patterns", "repost_patterns"):
                if isinstance(user.get(k), list) and user[k]:
                    cfg[k] = [str(x) for x in user[k]]
        except Exception as e:
            print("[opinion] 警告：--institutional 读取失败（%s），使用内置规则" % e)
    return cfg


_NEG_PREFIX_RE = re.compile(r"[不没无未别莫非](?:是|并|曾|会|能|再)?$")


def _negated_kws(text, hits):
    """返回 hits 中被否定词紧邻前置修饰的关键词（防呆标记，供 LLM 复核，不自动改判）。"""
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


def _hit(kw, text):
    """关键词命中判定：re: 前缀走正则（提升召回，v0.3）。"""
    if kw.startswith("re:"):
        try:
            return re.search(kw[3:], text) is not None
        except re.error:
            return False
    return kw in text


def classify(text, stances):
    """单标签聚类。返回 (立场, 命中关键词列表, 疑似否定修饰的关键词列表)。

    命中关键词最多者胜；平票按 stances 插入顺序取先者；无命中归「未识别」。
    注意：这个返回值只表示"词典没命中"，**不表示"与事件无关"**——相关性在上游
    relevance_gate.py 已经判过。旧名「无关/其他」会让读者以为这批是噪声（v0.4 改名）。
    """
    best, best_n, best_kws, best_neg = None, 0, [], []
    for s, kws in stances.items():
        hits = [k for k in kws if _hit(k, text)]
        neg = _negated_kws(text, hits)
        if len(hits) > best_n:
            best, best_n, best_kws, best_neg = s, len(hits), hits, neg
    if best is None:
        return OTHER, [], []
    return best, best_kws, best_neg


# ---------- 规则层（v0.4）：口语判据 ----------
# 为什么需要它：中文 UGC 表态很少用"标签词"（诬告/碰瓷/和稀泥），主流写法是
# 「复述动作 + 价值判断」——"她揪着孩子脖子一分钟" + "就是霸凌"，一个标签词都不含。
# 单靠标签词表，这类表达整批落进"未识别"。规则层用 {词 + 正则模式} 表达同一批立场，
# 但**不并入词典层**：两层各自出数、各自披露，读者能看到占比的下界与上界。
def load_rules(path=""):
    """加载规则层 json：{立场: {"positive": [词], "patterns": [正则]}, ..., "noise_patterns": [...]}。"""
    if not path:
        return {}, []
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except Exception as e:
        print("[opinion] 警告：--rules 读取失败（%s），本次不出规则层" % e)
        return {}, []
    if not isinstance(cfg, dict):
        return {}, []
    noise = cfg.pop("noise_patterns", []) or []
    return cfg, noise


def classify_rules(text, rules):
    """规则层单标签判定。返回 (立场, 命中说明) 或 (None, "")。"""
    best, best_n, best_hits = None, 0, []
    for s, spec in (rules or {}).items():
        if not isinstance(spec, dict):
            continue
        hits = [w for w in (spec.get("positive") or []) if w and w in text]
        for pat in (spec.get("patterns") or []):
            try:
                if re.search(pat, text):
                    hits.append(pat)
            except re.error:
                continue
        if len(hits) > best_n:
            best, best_n, best_hits = s, len(hits), hits
    return (best, best_hits) if best else (None, [])


def split_residual(rows, noise_patterns):
    """把"未识别"记录拆成可核对的几段（v0.4）。

    旧实现把 702 条未命中记录统称「无关/其他」并当成一个立场画图，造成两个误读：
    ① 读者以为这 74% 是噪声（其实全是事件相关内容）；② 覆盖率 21.7% 被当成"方法上限"
    （其实分母里塞着几百条本就不含立场表达的反应/玩梗/轶事）。

    这里只做**能机械判定**的分类，绝不臆断"有没有立场"——那需要语义判断，必须靠抽样人工核，
    所以最后一段老实叫「未分类」，并给出抽样规模提示：
      · 纯反应（短句/纯表情/纯链接/求链接，机械可判）
      · 原样转述（较长且命中报道用语/引号复述，偏信息而非表态）
      · 未分类（其余：既可能含未命中的立场表达，也可能是不表态的闲聊——
        **不得称为"不含立场表达"**，必须抽样人工核后才能描述其构成）
    返回 (seg_dict, rows_of_waiting)。
    """
    reactive, relay, waiting = [], [], []
    for r in rows:
        c = (r.get("content") or "").strip()
        c_clean = re.sub(r"\[[^\]]{0,6}R?\]|#.*?#|https?://\S+|\s+", " ", c).strip()
        if len(c_clean) < 6:
            reactive.append(r)
            continue
        if any(re.search(p, c_clean) for p in (noise_patterns or [])):
            reactive.append(r)
            continue
        # 原样转述：长文 + 报道用语（媒体口径复述，属信息供给而非个人表态）
        if len(c_clean) > 120 and re.search(r"(报道|记者|来源[:：]|据.{0,8}(称|报道)|评论员|时评|快评)", c_clean):
            relay.append(r)
            continue
        waiting.append(r)
    seg = {
        "纯反应（短句/表情/链接）": len(reactive),
        "原样转述（长文报道口径）": len(relay),
        "未分类（待抽样人工核）": len(waiting),
    }
    return seg, waiting


def _canon_url(u):
    """规范化引用地址：去掉会话性查询串（xsec_token 等），保留稳定路径；
    同时避免 href 里出现 &amp; 触发 check_report W4 的「截断实体」误判。"""
    u = str(u or "").strip()
    for sep in ("?", "#"):
        if sep in u:
            u = u.split(sep, 1)[0]
    return u


def _resolve_ref_url(rec, id2url):
    """样本可溯源地址：自身 url 优先；评论无永久链接时回指其父帖（parent_id → 父帖 url）。"""
    own = _canon_url(rec.get("url"))
    if own:
        return own, "own-url"
    pid = str(rec.get("parent_id") or "").strip()
    if pid:
        pu = id2url.get((rec.get("platform"), pid))
        if pu:
            return _canon_url(pu), "parent-post"
    return "", "none"


def is_institutional(rec, cfg):
    """机构/媒体帖判定。返回 (bool, 理由)。"""
    au = rec.get("author", "") or ""
    txt = rec.get("content", "") or ""
    for pat in cfg["name_patterns"]:
        if pat and pat in au:
            return True, "账号名机构特征"
    for pat in cfg["repost_patterns"]:
        try:
            m = re.search(pat, txt)
        except re.error:
            m = None
        if m and ("【" in txt or "#" in txt):
            return True, "报道用语+话题标签"
    return False, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--event", default="")
    ap.add_argument("--stances", default="", help="事件定制立场词典 json（{\"立场\": [\"关键词\",...]}）；"
                                                  "关键词支持 re: 前缀正则")
    ap.add_argument("--institutional", default="", help="机构识别规则 json（可选，缺省用内置启发式）")
    ap.add_argument("--rules", default="", help="规则层口语判据 json（可选）："
                                                "{\"立场\": {\"positive\": [词], \"patterns\": [正则]}, "
                                                "\"noise_patterns\": [正则]}；与词典层并列输出，不合并")
    ap.add_argument("--no-split", action="store_true",
                    help="退回旧单层行为：不剥离机构帖与未归类残差，全部计入立场分布")
    ap.add_argument("--total-raw", type=int, default=0,
                    help="语料治理前的原始条数（用于报告「治理剔除噪声」计数，可选）")
    ap.add_argument("--mid-samples", type=int, default=12, help="残差中位段抽样条数（默认 12）")
    args = ap.parse_args()

    stances = load_stances(args.stances)
    if not stances:
        print("[opinion] 错误：未提供立场词典（--stances 缺失或 config.json stance_keywords 为空）。")
        print("[opinion] 立场词典须按事件定制：依据采集内容与首轮搜索的观点分歧构造 {立场: [关键词,...]}，")
        print('[opinion] 例如：opinion.py --in <normalized.jsonl> --out <opinion.json> --event "事件" --stances data/<event_id>/stances.json')
        return 1
    inst_cfg = load_institutional(args.institutional)
    rules_file = args.rules
    if not rules_file:
        cand = os.path.join(os.path.dirname(os.path.abspath(args.out)), "opinion_rules.json")
        if os.path.exists(cand):
            rules_file = cand
    rules, noise_patterns = load_rules(rules_file)
    args.rules = rules_file

    recs = []
    with open(args.src, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                recs.append(json.loads(line))

    # 帖子 id → url（供评论回指父帖）
    id2url = {}
    for r in recs:
        u = _canon_url(r.get("url"))
        if u and not r.get("parent_id"):
            id2url.setdefault((r.get("platform"), str(r.get("id") or "")), u)

    kw_df = Counter()
    timeline = defaultdict(int)
    day_stance = defaultdict(lambda: defaultdict(int))
    A, B, C = [], [], []          # 个人表达 / 机构帖 / 未识别残差
    inst_reason = Counter()
    labels = {}
    neg_total = 0
    R = []                        # 规则层命中（与 A 层并列，不合并）
    rule_dist = defaultdict(list)
    rule_df = Counter()

    for r in recs:
        text = r.get("content", "") or ""
        label, matched, negated = classify(text, stances)
        neg_total += len(negated)
        for kws in stances.values():
            for kw in kws:
                if _hit(kw, text):
                    kw_df[kw] += 1
        if text and r.get("time"):
            timeline[str(r["time"])[:10]] += 1
        labels[id(r)] = (label, matched, negated)
        inst, why = is_institutional(r, inst_cfg)
        if inst:
            inst_reason[why] += 1
            B.append(r)
        elif label == OTHER:
            C.append(r)
        else:
            A.append(r)
        # 规则层：对全部非机构个人记录独立跑一遍（口径与词典层一致：非机构帖）
        if rules and not inst:
            rl, rh = classify_rules(text, rules)
            if rl:
                R.append(r)
                rule_dist[rl].append(r)
                for h in rh:
                    rule_df[h] += 1
            if r.get("time"):
                day_stance[str(r["time"])[:10]][label] += 1

    total = len(recs)
    if args.no_split:
        # 旧单层行为：全部记录进分母，未归类作为一个立场参与分布
        A, B, C = recs, [], []
        day_stance = defaultdict(lambda: defaultdict(int))
        for r in A:
            label = labels[id(r)][0]
            if r.get("time"):
                day_stance[str(r["time"])[:10]][label] += 1

    # ---- 立场分布（分母 = A 层）----
    by_stance = defaultdict(list)
    for r in A:
        by_stance[labels[id(r)][0]].append(r)
    denom = len(A)
    dist = []
    for s, rows in sorted(by_stance.items(), key=lambda kv: -len(kv[1])):
        cnt = len(rows)
        lk = int(sum(((r.get("metrics") or {}).get("likes", 0) or 0) for r in rows))
        samples = sorted(rows, key=lambda r: -(((r.get("metrics") or {}).get("likes", 0) or 0)))[:3]
        smp = []
        for r in samples:
            ref_url, how = _resolve_ref_url(r, id2url)
            lab, matched, negated = labels[id(r)]
            smp.append({"author": r.get("author", ""), "likes": (r.get("metrics") or {}).get("likes", 0),
                        "content": (r.get("content") or "")[:300], "url": _canon_url(r.get("url")),
                        "platform": r.get("platform", ""), "id": str(r.get("id") or ""),
                        "ref_url": ref_url, "ref_how": how, "matched": matched, "negated": negated})
        dist.append({
            "stance": s, "count": cnt, "pct": round(cnt / denom * 100, 1) if denom else 0,
            "likes": lk, "like_pct": 0.0,
            "minority": denom > 0 and cnt / denom * 100 < 10,
            "top_samples": smp,
        })
    like_sum = sum(d["likes"] for d in dist) or 1
    for d in dist:
        d["like_pct"] = round(d["likes"] / like_sum * 100, 1)

    # ---- 残差诊断（只取个人 UGC）+ 三段拆分（v0.4）----
    unc = [r for r in C if not is_institutional(r, inst_cfg)[0]]
    seg, waiting = split_residual(unc, noise_patterns)
    # 可判集合：个人表达（词典命中）+ 规则层命中 + 待判观点。
    # 覆盖率的分母必须用它，而不是"全部语料"——后者把几百条本就不含立场表达的反应/玩梗/轶事
    # 也算成"我们没判出来"，于是 74% 的"未识别"被误读成"方法只能覆盖两成"（v0.4 修）。
    rule_only = [r for r in R if labels[id(r)][0] == OTHER]
    judgeable = len(A) + len(rule_only) + len(waiting)
    vocab = [k[3:] if k.startswith("re:") else k for kws in stances.values() for k in kws]
    blob = " ".join((r.get("content") or "") for r in unc)
    grams = Counter(g for g in re.findall(r"[\u4e00-\u9fff]{2,4}", blob) if g not in _STOP)
    top = [{"gram": g, "df": c} for g, c in grams.most_common(120)]
    diff = [x for x in top if not any(x["gram"] in k or k in x["gram"] for k in vocab)][:40]
    band = sorted(unc, key=lambda r: ((r.get("metrics") or {}).get("likes", 0) or 0))
    lo = int(len(band) * 0.45)
    mid = []
    for r in band[lo:lo + max(0, args.mid_samples)]:
        ru, _ = _resolve_ref_url(r, id2url)
        mid.append({"author": r.get("author", ""), "likes": (r.get("metrics") or {}).get("likes", 0) or 0,
                    "content": (r.get("content") or "")[:160], "url": _canon_url(r.get("url")),
                    "ref_url": ru})

    result = {
        "event": args.event,
        "total_records": total,
        "denominator": denom,
        "coverage_rate": round(denom / total * 100, 1) if total else 0,
        "coverage_of_judgeable": round(denom / judgeable * 100, 1) if judgeable else 0,
        "judgeable_denominator": judgeable,
        "stance_distribution": dist,
        "institutional_layer": {"count": len(B), "reasons": dict(inst_reason), "note": inst_cfg["note"]},
        "residual": {
            "count": len(C), "pct_of_total": round(len(C) / total * 100, 1) if total else 0,
            "split": {"机构帖未识别": sum(1 for r in C if is_institutional(r, inst_cfg)[0]),
                      "个人 UGC 未识别": len(unc),
                      "语料治理阶段剔除的无关噪声": max(0, (args.total_raw or total) - total)},
            "composition": seg,
            "composition_note": "未识别 = 词典没命中，**不是**与事件无关（相关性已在上游治理）。"
                                "「未分类」段既可能含未命中的立场表达，也可能是不表态的闲聊——"
                                "**不得称为「不含立场表达」**，必须抽样人工核（建议 30 条并分层）后才能描述其构成；"
                                "在此之前不得把它当噪声，也不得画进立场占比。",
            "diagnostics": {"top_grams": top[:40], "grams_outside_vocabulary": diff, "mid_likes_samples": mid},
        },
        "rule_layer": ({
            "rules_file": args.rules,
            "count": len(R),
            "count_field": len(rule_only),
            "pct_of_judgeable": round(len(R) / judgeable * 100, 1) if judgeable else 0,
            "distribution": [{"stance": s, "count": len(rows),
                              "pct": round(len(rows) / len(R) * 100, 1) if R else 0,
                              "minority": len(rows) / len(R) * 100 < 10 if R else False,
                              "sample": (rows[0].get("content") or "")[:120] if rows else ""}
                             for s, rows in sorted(rule_dist.items(), key=lambda kv: -len(kv[1]))],
            "matched_terms": dict(sorted(rule_df.items(), key=lambda x: -x[1])),
            "status": "未抽检",
            "note": "规则层与词典层**并列不合并**：词典层是标签词匹配（可复现下界），规则层是口语判据。"
                    "正则无法判定立场，只能命中「像」某种立场的表述——实测误判率约 2–3 成（如把"
                    "「三岁儿子手肘碰到女士」判成儿童边界议题、把「调解失败」的事实复述判成质疑基层）。"
                    "故规则层在人工抽检（每立场 10 条）并写出误判率之前，**只能作为占比上界**呈现，"
                    "不得单独下判断；抽检结论回填本字段的 status 与 precision。",
        } if rules else None),
        "stance_timeline": {d: dict(day_stance[d]) for d in sorted(day_stance)},
        "timeline": dict(sorted(timeline.items())),
        "note": "四层口径（v0.3 默认）：A 个人表达（立场分母）· B 机构帖（不计入）· C 未识别残差 · D 治理剔除噪声。"
                "立场占比分母为 A 层样本数；coverage_rate=A/全部语料（保守下界），"
                "coverage_of_judgeable=A/可判集合（可判集合 = A + 规则层独有 + 待判观点），两个都须披露（v0.4）。"
                "单标签聚类：命中关键词最多者胜，平票按词典插入顺序；少数派阈值 10%；"
                "样本 negated 字段 = 命中词被否定词紧邻修饰（疑似反讽/否定，防呆提示，供 LLM 复核，不自动改判）。",
    }

    zero_hit, df_all = [], {}
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

    lines = ["# 舆论立场聚类：%s" % args.event, "",
             "> 共 %d 条记录 ｜ 立场分母（个人表达）%d 条 ｜ 归类覆盖率 %.1f%%"
             % (total, denom, result["coverage_rate"]), ""]
    for d in dist:
        flag = " [少数派]" if d["minority"] else ""
        lines.append("## %s（%.1f%% · %d 条 · 赞同 %d）%s" % (d["stance"], d["pct"], d["count"], d["likes"], flag))
        for s in d["top_samples"]:
            tag = "  [命中: %s]" % "/".join(s["matched"]) if s["matched"] else ""
            neg = "  [⚠疑似否定]" if s.get("negated") else ""
            ref = "  [%s]" % s["ref_url"] if s.get("ref_url") else ""
            lines.append("- [赞%d] %s：%s%s%s%s" % (
                s["likes"], s["author"], s["content"].replace("\n", " ")[:120], tag, neg, ref))
        lines.append("")
    lines.append("## 未识别残差（%.1f%% · %d 条）" % (result["residual"]["pct_of_total"], len(C)))
    lines.append("")
    lines.append("拆解：%s" % json.dumps(result["residual"]["split"], ensure_ascii=False))
    lines.append("")
    lines.append("构成（v0.4）：%s" % json.dumps(seg, ensure_ascii=False))
    lines.append("")
    lines.append("口径：可判集合 %d 条 → 词典层覆盖 %.1f%%（占全部语料 %.1f%%）。"
                 "「未识别」只表示词典没命中，不代表与事件无关。"
                 % (judgeable, result["coverage_of_judgeable"], result["coverage_rate"]))
    lines.append("")
    if result.get("rule_layer"):
        rl = result["rule_layer"]
        lines.append("## 规则层（口语判据，与词典层并列不合并）")
        lines.append("")
        lines.append("命中 %d 条（其中词典层未识别的 %d 条）｜占可判集合 %.1f%%｜文件 %s"
                     % (rl["count"], rl["count_field"], rl["pct_of_judgeable"], rl["rules_file"]))
        for d in rl["distribution"]:
            lines.append("- %s：%d 条（%.1f%%）" % (d["stance"], d["count"], d["pct"]))
        lines.append("")
        lines.append("⚠ 规则层须人工抽检（每立场 10 条）并写出误判率后才可作为结论依据；未抽检时只能读作占比上界。")
        lines.append("")
    lines.append("词表外高频词 Top20：" + "、".join("%s(%d)" % (x["gram"], x["df"])
                                                    for x in diff[:20]))
    lines.append("")
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("[opinion] 共 %d 条 → 词典层分母 %d 条（占全部语料 %.1f%% · 占可判集合 %.1f%%）"
          "｜机构帖 %d ｜未识别 %d ｜%d 个立场"
          % (total, denom, result["coverage_rate"], result["coverage_of_judgeable"],
             len(B), len(C), len(dist)))
    print("[opinion] 未识别构成：%s（未识别 ≠ 与事件无关，相关性已在上游判过）" % seg)
    if result.get("rule_layer"):
        rl = result["rule_layer"]
        print("[opinion] 规则层：命中 %d 条（词典层未识别的 %d 条）｜%d 个立场｜占可判集合 %.1f%%——"
              "须人工抽检误判率后才可用作结论"
              % (rl["count"], rl["count_field"], len(rl["distribution"]), rl["pct_of_judgeable"]))
    if neg_total:
        print("[opinion] 防呆提示：%d 条样本命中词疑似被否定修饰（negated 字段），请 LLM 复核" % neg_total)
    if zero_hit:
        print("[opinion] 词典质检警告：%d/%d 个关键词零命中（df=0）：%s"
              % (len(zero_hit), len(df_all), "、".join(zero_hit)))
    else:
        print("[opinion] 词典质检通过：%d 个关键词均有命中" % len(df_all))
    print("[opinion] → %s.json / .md" % base)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
