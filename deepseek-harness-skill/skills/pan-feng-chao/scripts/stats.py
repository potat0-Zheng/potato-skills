# -*- coding: utf-8 -*-
"""pan-feng-chao 统计工具（纯标准库，零第三方依赖）。

为什么单独成文件：占比、抽样误差、置信区间、编码信度这几件事原来散在
opinion.py / spotcheck.py 里各算一遍（且都用 1.96*se 的正态近似）。抽出来共用后，
两个脚本对"同一个数字"只有一个算法，改动也只改一处。

本文件只做数学：不读文件、不打印、不依赖项目内其他模块。

口径说明（写在代码里，供复核）：
  · 单比例区间用 **Wilson 得分区间**，不用正态近似——小样本（n<30）与极端比例
    （p 接近 0/1）下正态近似会给出越界或过窄的区间，而舆论抽检的样本量恰好常年落在这个区间。
  · 分层估计用**后分层**（post-stratification）：按已知层大小加权，并给出设计效应，
    让"按平台 × 点赞档分层抽样"真正体现在数字上，而不是只体现在抽样计划里。
    分层方差的区间用正态近似（分层估计的标准做法），并在返回值里标注 method 以示区别。
  · 信度用 Cohen κ（两人、二分类）与 Krippendorff α（nominal，支持两人以上/多类别）。
    两者都用"观测不一致 / 期望不一致"的定义，数值不可跨口径比较。
"""
import math
from collections import Counter

Z95 = 1.959963984540054


# ---------------- 单比例区间 ----------------

def wilson_interval(k, n, z=Z95):
    """Wilson 得分区间，返回比例区间 (lo, hi) ∈ [0,1]。k=命中数，n=样本数。"""
    n = int(n)
    if n <= 0:
        return (0.0, 1.0)
    k = max(0, min(int(k), n))
    p = k / float(n)
    d = 1.0 + z * z / n
    center = p + z * z / (2.0 * n)
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    lo, hi = (center - half) / d, (center + half) / d
    return (max(0.0, lo), min(1.0, hi))


def wilson_pct(k, n, digits=1, z=Z95):
    """Wilson 区间的百分点形式，便于直接写进工件（如 (32.0, 48.0)）。"""
    lo, hi = wilson_interval(k, n, z)
    return (round(lo * 100, digits), round(hi * 100, digits))


def scale_interval(interval, total):
    """把比例区间放大到一个已知总体（如"未识别层 N 条"），返回整数条数区间。"""
    lo, hi = interval
    total = int(total or 0)
    return [max(0, int(math.floor(lo * total))), min(total, int(math.ceil(hi * total)))]


def fmt_ci(interval, digits=1):
    """区间 → '32.0–48.0' 形式字符串（供 markdown/控制台）。"""
    if not interval:
        return "—"
    lo, hi = interval
    return "%.*f–%.*f" % (digits, lo, digits, hi)


# ---------------- 分层（后分层）估计 ----------------

def stratified_proportion(strata, z=Z95):
    """后分层比例估计。

    strata: [{"name": 层名, "N": 该层总体条数, "n": 该层抽样条数, "k": 该层命中条数}, ...]
    返回 None 表示无法估计（无层或层总体为 0）。

    注意：未抽到的层（n=0）权重计入 total_N，但不贡献比例，其条数在 unsampled_N 中披露——
    静默把它们当成"抽样到的层"是最常见的隐性偏倚来源。
    """
    rows = [s for s in (strata or []) if isinstance(s, dict) and int(s.get("N") or 0) > 0]
    total_N = sum(int(s["N"]) for s in rows)
    if not rows or total_N <= 0:
        return None
    p, var, n_samp, unsampled_N = 0.0, 0.0, 0, 0
    for s in rows:
        N, n, k = int(s["N"]), int(s.get("n") or 0), int(s.get("k") or 0)
        w = N / float(total_N)
        if n <= 0:
            unsampled_N += N
            continue
        ph = max(0.0, min(1.0, k / float(n)))
        p += w * ph
        var += (w ** 2) * (ph * (1.0 - ph) / n)
        n_samp += n
    se = math.sqrt(var)
    var_srs = (p * (1.0 - p) / n_samp) if n_samp else 0.0
    lo, hi = max(0.0, p - z * se), min(1.0, p + z * se)
    return {
        "method": "post-stratified (normal approx on stratified variance)",
        "p": p,
        "p_pct": round(p * 100, 1),
        "se": se,
        "ci95": (lo, hi),
        "ci95_pct": (round(lo * 100, 1), round(hi * 100, 1)),
        "design_effect": round(var / var_srs, 3) if var_srs > 0 else None,
        "effective_n": round(p * (1.0 - p) / var, 1) if var > 0 else None,
        "n_sampled": n_samp,
        "n_strata": len(rows),
        "total_N": total_N,
        "unsampled_N": unsampled_N,
    }


# ---------------- 编码信度 ----------------

def cohen_kappa(pairs):
    """Cohen κ（两人、同一批对象、类别可多可少）。

    pairs: [(判读A, 判读B), ...]，元素为可哈希标签（如 True/False 或 "对"/"错"）。
    退化情形（双方标签完全一致且只有单一类别，pe=1）下 κ 无定义，返回 None 并标 degenerate。
    """
    n = len(pairs or [])
    if n == 0:
        return {"n": 0, "kappa": None, "po": None, "pe": None, "categories": [], "degenerate": True}
    po = sum(1 for a, b in pairs if a == b) / float(n)
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    cats = sorted(set(ca) | set(cb), key=lambda x: str(x))
    pe = sum((ca[c] / float(n)) * (cb[c] / float(n)) for c in cats)
    if 1.0 - pe < 1e-12:
        return {"n": n, "kappa": (1.0 if po >= 1.0 else None), "po": po, "pe": pe,
                "categories": [str(c) for c in cats], "degenerate": True}
    return {"n": n, "kappa": (po - pe) / (1.0 - pe), "po": po, "pe": pe,
            "categories": [str(c) for c in cats], "degenerate": False}


def krippendorff_alpha_nominal(units):
    """Krippendorff α（nominal）。units: [[判读1, 判读2, ...], ...]，每个单元至少 2 个判读。

    返回 alpha（无法估计时为 None）与 Do/De 便于复核。
    """
    us = [list(u) for u in (units or []) if u is not None and len(u) >= 2]
    if not us:
        return {"alpha": None, "do": None, "de": None, "n_units": 0, "n_values": 0}
    coinc = Counter()
    for u in us:
        m = len(u)
        for i in range(m):
            for j in range(m):
                if i != j:
                    coinc[(u[i], u[j])] += 1.0 / (m - 1)
    n = sum(coinc.values())
    if n <= 1:
        return {"alpha": None, "do": None, "de": None, "n_units": len(us), "n_values": int(n)}
    do = sum(v for (c, k), v in coinc.items() if c != k) / n
    marg = Counter()
    for (c, _k), v in coinc.items():
        marg[c] += v
    de = 0.0
    for c in marg:
        for k in marg:
            if c != k:
                de += marg[c] * marg[k]
    de /= (n * (n - 1.0))
    alpha = (1.0 - do / de) if de > 0 else None
    return {"alpha": alpha, "do": do, "de": de, "n_units": len(us), "n_values": int(n)}


# ---------------- 两比例之差（"明显高于"这句话的资格） ----------------

def newcombe_diff_ci(k1, n1, k2, n2, z=Z95):
    """两**独立**样本比例之差的 Newcombe 混合得分区间（method 10，square-and-add）。

    为什么不用 Wald（p1−p2 ± z·se）：小样本与极端比例下 Wald 区间会明显失真、越界，
    而抽检样本量恰好常年落在那一段。Newcombe 的做法是两端各用 Wilson 区间
    （已处理小样本），再用"平方相加开根号"合并，端点单调、覆盖率表现更好。
    参考：Newcombe 1998, Stat Med 17:2635；R 包 Epi::ci.pd（method="score"）。
    返回比例区间 (lo, hi)。
    """
    if n1 <= 0 or n2 <= 0:
        return (-1.0, 1.0)
    p1 = max(0.0, min(1.0, k1 / float(n1)))
    p2 = max(0.0, min(1.0, k2 / float(n2)))
    l1, u1 = wilson_interval(k1, n1, z)
    l2, u2 = wilson_interval(k2, n2, z)
    d = p1 - p2
    lo = d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return (max(-1.0, lo), min(1.0, hi))


def multinomial_diff_ci(k1, k2, n, z=Z95):
    """**同一批样本内**两个类别占比之差的区间（多类别单标签场景，即立场占比）。

    这里不能套 Newcombe：两个立场来自同一次多项抽样，彼此**不独立**，
    Var(p1−p2) = [p1 + p2 − (p1 − p2)²] / n（协方差项 +2p1p2 合并后即此式）。
    用独立公式会把区间算窄，于是"支持 50% vs 反对 28%"会被误报成"明显更高"。
    """
    n = int(n)
    if n <= 0:
        return (-1.0, 1.0)
    p1, p2 = k1 / float(n), k2 / float(n)
    d = p1 - p2
    var = max(0.0, (p1 + p2 - d * d) / n)
    se = math.sqrt(var)
    return (max(-1.0, d - z * se), min(1.0, d + z * se))


def diff_verdict(interval):
    """把差值区间翻成一句可直接引用的判断（0 落在区间内 = 不得声称有差异）。"""
    lo, hi = interval
    if lo > 0:
        return "可声称前者高于后者（区间下界 %.1f 个百分点 > 0）" % (lo * 100)
    if hi < 0:
        return "可声称前者低于后者（区间上界 %.1f 个百分点 < 0）" % (hi * 100)
    return "**不得声称两者有差异**：区间跨 0（%.1f 至 %.1f 个百分点）" % (lo * 100, hi * 100)


# ---------------- 以"人"为单位的方差修正（同一作者多条记录） ----------------

def clustered_proportion(counts, sizes, z=Z95):
    """按**簇**（这里是一个作者）修正的比例与区间。

    同一作者的多条帖子彼此高度相关，把它们当独立样本会低估标准误
    （经典结果 deff = 1 + (m̄−1)·ρ）。这里用比值估计量的线性化方差（簇为初级抽样单元）：
        p̂ = Σy_c / Σm_c
        Var(p̂) ≈ [n/(n−1)] · Σ_c (y_c − p̂·m_c)² / (Σm_c)²
    与 survey 包"以簇为 PSU 的 Taylor 线性化"是同一件事，只是手写、零依赖。
    counts/sizes：每个簇的命中数与记录数，长度一致。
    返回 None 表示簇数不足（<2）。
    """
    pairs = [(int(c), int(m)) for c, m in zip(counts or [], sizes or []) if int(m) > 0]
    n = len(pairs)
    if n < 2:
        return None
    total_m = sum(m for _c, m in pairs)
    if total_m <= 0:
        return None
    total_y = sum(c for c, _m in pairs)
    p = total_y / float(total_m)
    var = (n / float(n - 1)) * sum((c - p * m) ** 2 for c, m in pairs) / (total_m ** 2)
    se = math.sqrt(max(0.0, var))
    var_srs = (p * (1.0 - p) / total_m) if total_m else 0.0
    deff = (var / var_srs) if var_srs > 0 else None
    lo, hi = max(0.0, p - z * se), min(1.0, p + z * se)
    return {
        "method": "cluster-robust (author as PSU, ratio-estimator linearization)",
        "p": p, "p_pct": round(p * 100, 1), "se": se,
        "ci95": (lo, hi), "ci95_pct": (round(lo * 100, 1), round(hi * 100, 1)),
        "design_effect": round(deff, 3) if deff is not None else None,
        "effective_n": round(p * (1.0 - p) / var, 1) if var > 0 else None,
        "n_clusters": n, "n_records": total_m,
        "cluster_sizes": {"max": max(m for _c, m in pairs), "mean": round(total_m / float(n), 2)},
    }


# ---------------- 抽样计划诊断（抽样前就看出计划划不划算） ----------------

def neyman_allocation(sizes, n_total, props=None):
    """Neyman 最优分配（∝ N_h·S_h），诊断"按规模成比例分配"是否在浪费样本。

    props：各层预期层内比例；未知时取 0.5（二项方差最大，保守）。
    返回 (neyman_allocation, comparison)，后者含两种分配的方差比（>1 表示成比例分配更费）。
    """
    rows = [(i, int(s)) for i, s in enumerate(sizes or []) if int(s) > 0]
    n_total = int(n_total or 0)
    if not rows or n_total <= 0:
        return {}, {}
    pr = list(props or [])

    def _p(i):
        v = pr[i] if i < len(pr) and pr[i] is not None else 0.5
        return max(1e-6, min(1 - 1e-6, float(v)))

    tot_N = sum(N for _i, N in rows)
    weighted = {i: N * math.sqrt(_p(i) * (1 - _p(i))) for i, N in rows}
    tot_w = sum(weighted.values()) or 1.0
    ney, prop_alloc = {}, {}
    for i, N in rows:
        ney[i] = max(1, int(round(n_total * weighted[i] / tot_w)))
        prop_alloc[i] = max(1, int(round(n_total * N / float(tot_N))))

    def _var(alloc):
        return sum((N / float(tot_N)) ** 2 * _p(i) * (1 - _p(i)) / max(1, alloc.get(i, 1))
                   for i, N in rows)

    vn, vp = _var(ney), _var(prop_alloc)
    return ney, {"variance_neyman": vn, "variance_proportional": vp,
                 "variance_ratio": (vp / vn) if vn > 0 else None}


def required_n(p=0.5, margin=0.1, N=None, z=Z95):
    """给定"可接受的绝对误差"反推所需样本量；N 为总体规模时加有限总体校正（fpc）。"""
    p = max(1e-6, min(1 - 1e-6, float(p)))
    margin = float(margin)
    if margin <= 0:
        return None
    n = (z ** 2) * p * (1 - p) / (margin ** 2)
    if N:
        n = n / (1.0 + (n - 1.0) / float(N))
    return int(math.ceil(n))


def hhi(shares):
    """赫芬达尔—赫希曼指数（份额平方和，取值 0–1）。标准 HHI 为其 ×10000。"""
    ss = [max(0.0, float(s)) for s in (shares or [])]
    tot = sum(ss)
    if tot <= 0:
        return None
    return round(sum((s / tot) ** 2 for s in ss), 4)


# ---------------- 时序：变点筛查（CUSUM） ----------------

def change_points(series, min_span=3, max_points=5, h=4.0, min_day_n=1):
    """变点筛查：**二分段（binary segmentation）+ 两比例得分区间**。

    为什么不用 CUSUM（v0.7 早期实现，实测后被换掉）：
      CUSUM 需要一个"稳定基线" p0 与合适的 σ。在本场景里 p0 只能用全序列合并比例，
      于是一段**真实趋势**（前 6 天全支持、后 6 天全反对）会被逐日累积的正偏差
      在**第一天**就顶破阈值——实测在 12 天/每天 6 条的语料上连报 3 个假变点，
      而真正的切换日（第 6→7 天）反而被淹没。变点检测的经典解法是分段搜索，不是累积和。
    现在的做法：对每一段尝试所有可能分割点，用 Newcombe 独立区间比较两侧比例，
    取"区间不跨 0 且差异最大"的分割点，然后对该点两侧递归（受 min_span 与 max_points 约束）。
    这既是标准的二分段思路，输出也天然带"这个变点是否站得住"的判据（区间是否跨 0）。

    series: [{"day","k","n"}, ...]（按时间排序）。返回 {"change_points", "note", ...}
    """
    pts = [(str(x.get("day")), int(x.get("k") or 0), int(x.get("n") or 0))
           for x in (series or [])]
    pts = [p for p in pts if p[2] >= max(1, int(min_day_n))]
    need = max(2, int(min_span)) * 2
    if len(pts) < need:
        return {"change_points": [], "min_span": int(min_span),
                "note": "有效日数不足（%d < %d），不做变点筛查" % (len(pts), need)}
    total_k = sum(p[1] for p in pts)
    total_n = sum(p[2] for p in pts)
    points = []
    queue = [(0, len(pts))]
    while queue and len(points) < int(max_points):
        lo, hi = queue.pop(0)
        seg = pts[lo:hi]
        if len(seg) < need:
            continue
        best = None
        for i in range(int(min_span), len(seg) - int(min_span) + 1):
            a, b = seg[:i], seg[i:]
            ka, na = sum(x[1] for x in a), sum(x[2] for x in a)
            kb, nb = sum(x[1] for x in b), sum(x[2] for x in b)
            if na <= 0 or nb <= 0:
                continue
            ci = newcombe_diff_ci(kb, nb, ka, na)
            if ci[0] > 0 or ci[1] < 0:                 # 两侧差异的区间不跨 0 才认
                score = abs((kb / nb) - (ka / na))
                if best is None or score > best[0]:
                    best = (score, i, a, b, ka, na, kb, nb, ci)
        if best is None:
            continue
        _score, i, a, b, ka, na, kb, nb, ci = best
        pa, pb = ka / na, kb / nb
        points.append({
            "day": b[0][0],
            "direction": "上升" if pb > pa else "下降",
            "before": {"days": [a[0][0], a[-1][0]], "k": ka, "n": na, "pct": round(pa * 100, 1)},
            "after": {"days": [b[0][0], b[-1][0]], "k": kb, "n": nb, "pct": round(pb * 100, 1)},
            "diff_pp": round((pb - pa) * 100, 1),
            "diff_ci95_pp": [round(ci[0] * 100, 1), round(ci[1] * 100, 1)],
            "strong": True,
            "verdict": diff_verdict(ci),
        })
        queue.append((lo, lo + i))
        queue.append((lo + i, hi))
    points.sort(key=lambda x: x["day"])
    return {
        "change_points": points,
        "min_span": int(min_span),
        "pooled_pct": round(total_k / total_n * 100, 1) if total_n else None,
        "method": "binary segmentation (recursive best split) + Newcombe CI on both sides",
        "note": "变点是**候选提示**：每条都带前后区间，区间跨 0 的不会被输出；"
                "但变点检测不能证明因果，也不能替代「多时点协议」，跨报告不可比。",
    }


def cusum_change_points(series, h=4.0, min_span=2, min_day_n=1):
    """（保留但**不推荐**）两向 CUSUM 变点筛查。

    实测缺陷：需要稳定基线 p0，而真实趋势语料里 p0 只能用全序列合并比例，
    会在趋势起始日就顶破阈值、连报假变点（12 天语料上报 3 个，真正切换日被淹没）。
    仅为对照与排查保留；常规请用 change_points()（二分段 + 得分区间）。
    """
    pts = [(str(x.get("day")), int(x.get("k") or 0), int(x.get("n") or 0))
           for x in (series or [])]
    pts = [p for p in pts if p[2] >= max(1, int(min_day_n))]
    if len(pts) < max(4, int(min_span) * 2):
        return {"change_points": [], "note": "有效日数不足（<%d）" % max(4, min_span * 2)}
    total_k = sum(p[1] for p in pts)
    total_n = sum(p[2] for p in pts)
    if total_n <= 0:
        return {"change_points": [], "note": "分母为 0"}
    p0 = total_k / float(total_n)
    sd = math.sqrt(max(1e-12, p0 * (1 - p0)))
    out, s, start = [], 0.0, 0
    for i, (day, k, n) in enumerate(pts):
        s += (k - n * p0) / (sd * math.sqrt(n))
        if abs(s) >= h:
            a, b = pts[start:i + 1], pts[i + 1:]
            if len(a) >= int(min_span) and len(b) >= int(min_span):
                ka, na = sum(x[1] for x in a), sum(x[2] for x in a)
                kb, nb = sum(x[1] for x in b), sum(x[2] for x in b)
                ci = newcombe_diff_ci(kb, nb, ka, na)
                out.append({"day": day, "cusum": round(s, 2), "threshold": h,
                            "before": {"pct": round(ka / na * 100, 1) if na else None},
                            "after": {"pct": round(kb / nb * 100, 1) if nb else None},
                            "diff_ci95_pp": [round(ci[0] * 100, 1), round(ci[1] * 100, 1)],
                            "strong": bool(ci[0] > 0 or ci[1] < 0), "verdict": diff_verdict(ci)})
            s, start = 0.0, i + 1
    return {"change_points": out, "p0": round(p0 * 100, 1), "h": h,
            "method": "two-sided CUSUM（对照用，不推荐）",
            "note": "CUSUM 在真实趋势语料上会从趋势首日误报，故仅作对照；请用 change_points()。"}


def reliability_note(kappa=None, alpha=None):
    """给信度数值配一句可引用的判读话术（避免把低信度当成结论依据）。"""
    worst = None
    for v in (kappa, alpha):
        if isinstance(v, (int, float)):
            worst = v if worst is None else min(worst, v)
    if worst is None:
        return "无法计算信度（判读数据不足）"
    if worst >= 0.8:
        return "信度良好（≥0.80）：标签可作为结论依据"
    if worst >= 0.667:
        return "信度可接受（0.667–0.80）：标签可用，但须同时披露信度值"
    if worst >= 0.4:
        return "信度偏低（0.40–0.667）：只能作为探索性描述，不得作为主结论"
    return "信度不可接受（<0.40）：标签体系或判读标准须先修订后重跑"
