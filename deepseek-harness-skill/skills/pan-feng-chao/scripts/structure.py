# -*- coding: utf-8 -*-
"""pan-feng-chao 传播结构分析（纯标准库）：声量 vs 人数、发文集中度、复制粘贴/近重复。

为什么要单列一个模块：舆论分析里"条数"和"人数"是两件事。一条刷屏账号能顶几十条，
只用条数算占比，等于让账号的活跃度冒充公众意见的分布。本模块只做**结构标记与披露**：

  · 绝不自动剔除任何记录、绝不自动改分母——"疑似同源文本/高频账号"是解释线索，不是判决；
  · 每条标记都可被人工复核（带 cluster id、账号名、条数与理由）。

近重复检测用 SimHash + 分桶（鸽笼原理：64 位分 4 段、汉明距离阈值 3 时至少有一段完全相同），
与主流近重复方案（Google simhash 思路、simhash-py / datasketch 一类库）同源，
但手写实现、零依赖；即便装了 datasketch 也不会自动启用——增强必须显式开、且要披露。
"""
import hashlib
import re
from collections import Counter, defaultdict

_URL = re.compile(r"https?://\S+")
_AT = re.compile(r"@[\w\u4e00-\u9fff\-_.]{1,30}")
_TOPIC = re.compile(r"#([^#]{1,40})#")
_BRACKET = re.compile(r"\[[^\]]{0,8}\]")
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\u4e00-\u9fff]+")


def normalize_text(s):
    """规范化文本：去链接/@/#话题标签/表情方括号/标点与空白，转小写。用于重复检测。"""
    s = str(s or "")
    s = _URL.sub(" ", s)
    s = _AT.sub(" ", s)
    s = _TOPIC.sub(r"\1", s)          # 话题标签只留内容（同话题不算同文，但保留其字面）
    s = _BRACKET.sub(" ", s)
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip().lower()
    return s


def shingles(text, k=4):
    """字符 k-gram（中文按字切分最稳，不需要分词器）。短于 k 时退化为整串。"""
    t = text or ""
    if not t:
        return []
    if len(t) <= k:
        return [t]
    return [t[i:i + k] for i in range(len(t) - k + 1)]


def _h64(token):
    return int.from_bytes(hashlib.md5(token.encode("utf-8")).digest()[:8], "big")


def simhash64(text, k=4, weights=None):
    """64 位 SimHash。weights 给出每个 token 的权重（如点赞数的对数），缺省等权。"""
    grams = shingles(normalize_text(text), k)
    if not grams:
        return 0
    counts = Counter(grams)
    vec = [0] * 64
    for g, c in counts.items():
        w = float(c) if weights is None else float(weights(g, c))
        h = _h64(g)
        for b in range(64):
            vec[b] += w if (h >> b) & 1 else -w
    out = 0
    for b in range(64):
        if vec[b] > 0:
            out |= (1 << b)
    return out


def hamming(a, b):
    return int(a ^ b).bit_count()


def near_duplicate_clusters(keys, sigs, threshold=3, min_len=8, texts=None, bands=4):
    """把近重复记录聚成簇。keys 与 sigs 一一对应；texts 给定时用于剔掉过短文本。

    分桶（鸽笼原理）：64 位切成 bands 段，汉明距离 ≤ bands−1 时至少有一段完全相同，
    故只需在同段同值的桶内两两比较——把 O(n²) 降成桶内比较，纯 Python 也能跑万级语料。
    返回 {key: cluster_id} 与簇列表。
    """
    idx = {k: i for i, k in enumerate(keys)}
    buckets = defaultdict(list)
    width = 64 // bands
    mask = (1 << width) - 1
    for i, s in enumerate(sigs):
        if texts is not None and len(texts[i] or "") < min_len:
            continue
        for b in range(bands):
            buckets[(b, (s >> (b * width)) & mask)].append(i)
    parent = list(range(len(keys)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[max(rx, ry)] = min(rx, ry)

    for _bk, members in buckets.items():
        if len(members) < 2:
            continue
        for a in range(len(members)):
            ia = members[a]
            for b in range(a + 1, len(members)):
                ib = members[b]
                if find(ia) == find(ib):
                    continue
                if hamming(sigs[ia], sigs[ib]) <= threshold:
                    union(ia, ib)
    groups = defaultdict(list)
    for i, k in enumerate(keys):
        if texts is not None and len(texts[i] or "") < min_len:
            continue
        groups[find(i)].append(k)
    out, clusters = {}, []
    cid = 0
    for _root, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(members) < 2:
            continue
        cid += 1
        name = "dup%02d" % cid
        for k in members:
            out[k] = {"cluster": name, "size": len(members), "representative": members[0]}
        clusters.append({"cluster": name, "size": len(members),
                         "keys": [{"platform": k[0], "id": k[1]} for k in members[:20]]})
    return out, clusters


def near_dup_map(rows, key_fn, text_fn, threshold=3, min_len=8):
    """近重复映射（机器用）：返回 {"flags": {key: {...}}, "clusters": [...], "stats": {...}}。

    flags 的键是调用方给的 key（如 platform+id 元组），供上层做"每个立场里有多少条落在簇内"。
    只标记，不删记录。

    v0.6.1 修正（P1）：长度闸门改判**规范化后**的文本。
    原先用原始文本长度做闸门，而 SimHash 用的是规范化文本——于是"只有链接/@/表情"的帖子
    （`https://t.co/xxxx`、`@某人 https://…`、`[doge][笑cry]`）原始长度过闸、规范化后却是空串，
    simhash 恒为 0，全部落进同一个桶、两两汉明距离 0 → **被判成一整个"同源文本簇"**。
    实测：100 条纯链接帖 → 100/100 标为近重复、1 个簇、100 个不同作者 → 被报成
    "跨账号同源，值得人工核"。那是假线索，会把人工核引到空处。
    改判规范化长度后这类记录直接不入比较，条数在 stats.not_compared_short_or_empty_text 里披露。
    """
    keys = [key_fn(r) for r in rows]
    texts = [str(text_fn(r) or "") for r in rows]
    norms = [normalize_text(t) for t in texts]
    exact = Counter(t for t in norms if t)
    sigs = [simhash64(t) for t in texts]
    # 闸门用规范化文本：与 SimHash 实际看到的文本保持一致
    flags, clusters = near_duplicate_clusters(keys, sigs, threshold=threshold,
                                              min_len=min_len, texts=norms)
    not_compared = sum(1 for t in norms if len(t) < min_len)
    # 给每个簇补上"成员里有几个不同账号"——这一条区分两种完全不同的现象：
    #   只有 1 个账号 = 自己在重复刷；多个账号 = 跨账号同源（可能是组织化，也可能是转述同一新闻）。
    key2row = {}
    for i, k in enumerate(keys):
        key2row.setdefault(k, rows[i])
    by_cluster = defaultdict(list)
    for k in keys:
        f = flags.get(k)
        if f:
            by_cluster[f["cluster"]].append(k)
    cross_records = self_records = 0
    enriched = []
    for c in clusters:
        members = by_cluster.get(c["cluster"], [])
        authors = sorted({str((key2row.get(k) or {}).get("author") or "(未署名)") for k in members})
        cross = len(authors) > 1
        if cross:
            cross_records += len(members)
        else:
            self_records += len(members)
        for k in members:
            flags[k]["cross_account"] = cross
            flags[k]["distinct_authors"] = len(authors)
        enriched.append({**c, "distinct_authors": len(authors),
                         "authors": authors[:10], "cross_account": cross})
    return {
        "flags": flags,
        "keys": keys,
        "clusters": enriched,
        "stats": {
            "records": len(rows),
            # "exact" 指**规范化后**同文（去掉链接/@/话题标记/标点空白后完全一致），
            # 不是原始字节相同——所以"同一句话加个表情"也会落进这一档。
            "exact_duplicate_records": sum(c for t, c in exact.items() if c > 1),
            "near_duplicate_records": sum(1 for k in keys if k in flags),
            "cluster_count": len(clusters),
            "largest_cluster_size": max((c["size"] for c in clusters), default=0),
            "cross_account_dup_records": cross_records,
            "same_author_repeat_records": self_records,
            # v0.6.1（P1）：规范化后不足 min_len 的记录**不参与近重复比较**（多为纯链接/@/表情帖，
            # 它们的 simhash 恒为 0，参与比较会把互不相干的帖子聚成一个假簇）。
            # 这里如实披露条数，避免读者把"没被判重复"读成"已比对且不重复"。
            "not_compared_short_or_empty_text": not_compared,
        },
    }


def duplication_report(dup_map, rows, key_fn, max_clusters=20, max_flags=200, threshold=3):
    """把 near_dup_map 的结果整理成可写进工件的报告（JSON 友好，只标记不改数）。"""
    flags = dup_map.get("flags") or {}
    stats_ = dict(dup_map.get("stats") or {})
    n = stats_.get("records") or 0
    stats_["near_duplicate_pct"] = round(stats_.get("near_duplicate_records", 0) / n * 100, 1) if n else 0
    by_author = Counter()
    for r in rows:
        if key_fn(r) in flags:
            by_author[str(r.get("author") or "(未署名)")] += 1
    cluster_of = []
    for r in rows:
        k = key_fn(r)
        f = flags.get(k)
        if not f:
            continue
        cluster_of.append({"platform": k[0], "id": k[1], "author": str(r.get("author") or ""),
                           "cluster": f["cluster"], "cluster_size": f["size"]})
    return {
        "method": "simhash64(char 4-gram) + banding(pigeonhole), hamming ≤ %d" % threshold,
        "records": n,
        "exact_duplicate_records": stats_.get("exact_duplicate_records", 0),
        "exact_definition": "规范化后同文（去链接/@/话题标记/标点空白后完全一致），非原始字节相同",
        "near_duplicate_records": stats_.get("near_duplicate_records", 0),
        "near_duplicate_pct": stats_.get("near_duplicate_pct", 0),
        "cross_account_dup_records": stats_.get("cross_account_dup_records", 0),
        "same_author_repeat_records": stats_.get("same_author_repeat_records", 0),
        "not_compared_short_or_empty_text": stats_.get("not_compared_short_or_empty_text", 0),
        "cluster_count": stats_.get("cluster_count", 0),
        "largest_cluster_size": stats_.get("largest_cluster_size", 0),
        "clusters": (dup_map.get("clusters") or [])[:max_clusters],
        "cluster_of": cluster_of[:max_flags],
        "cluster_of_truncated": len(cluster_of) > max_flags,
        "top_accounts_in_dup_clusters": [{"author": a, "records": c}
                                         for a, c in by_author.most_common(10)],
        "note": "仅作结构披露：同源文本可能来自组织化推动，也可能来自正常转述/引用同一新闻，"
                "**不得据此自动剔除记录或改动分母**。两个关键区分："
                "cross_account_dup_records（多个不同账号发同源文本 = 值得人工核的线索）与 "
                "same_author_repeat_records（同一账号自己重复 = 更可能是刷屏或复读，仍不算水军证据）；"
                "要下判断必须先人工抽样核并写进报告。"
                "not_compared_short_or_empty_text = 去掉链接/@/表情/标点后不足阈值的记录，"
                "它们**未参与**近重复比较（不是「比对过且不重复」）。",
    }


def author_report(rows, key_fn, label_fn, n_top=10, heavy_threshold=5):
    """声量 vs 人数：记录数、独立作者数、条/人比、发文集中度（HHI 与头部份额）。"""
    n_records = len(rows)
    per_author = defaultdict(list)
    for r in rows:
        per_author[str(r.get("author") or "(未署名)")].append(r)
    n_authors = len(per_author)
    sizes = [len(v) for v in per_author.values()]
    heavy = {a: len(v) for a, v in per_author.items() if len(v) >= heavy_threshold}
    heavy_records = sum(heavy.values())
    top = []
    for a, rs in sorted(per_author.items(), key=lambda kv: -len(kv[1]))[:n_top]:
        likes = sum(int(((r.get("metrics") or {}).get("likes", 0) or 0)) for r in rs)
        mix = Counter(label_fn(r) for r in rs)
        top.append({"author": a, "records": len(rs), "likes": likes,
                    "share_of_records": round(len(rs) / n_records * 100, 1) if n_records else 0,
                    "stance_mix": dict(mix.most_common(3)),
                    "sample": (rs[0].get("content") or "")[:80]})
    shares = [s / n_records for s in sizes] if n_records else []
    ordered = sorted(shares, reverse=True)
    return {
        "records": n_records,
        "unique_authors": n_authors,
        "posts_per_author": round(n_records / n_authors, 2) if n_authors else 0,
        "max_posts_by_one_author": max(sizes) if sizes else 0,
        "heavy_accounts": {"threshold": heavy_threshold, "accounts": len(heavy),
                           "records": heavy_records,
                           "pct_of_records": round(heavy_records / n_records * 100, 1) if n_records else 0},
        "concentration": {
            "hhi": _hhi(shares),
            "hhi_x10000": round((_hhi(shares) or 0) * 10000, 1),
            "top1_share_pct": round(ordered[0] * 100, 1) if ordered else 0,
            "top5_share_pct": round(sum(ordered[:5]) * 100, 1) if ordered else 0,
            "note": "HHI 为作者发文份额平方和（×10000 后的标准写法）。HHI 越高，"
                    "说明『声量』越集中在少数账号，条数占比越不能当人数占比读。",
        },
        "top_authors": top,
        "note": "「未署名」会并成一个虚拟作者，平台不返回作者名时该口径失真，须在报告中说明。",
    }


def _hhi(shares):
    ss = [max(0.0, float(s)) for s in (shares or [])]
    tot = sum(ss)
    return round(sum((s / tot) ** 2 for s in ss), 4) if tot > 0 else None


def candidate_clusters(rows, key_fn, text_fn, vocab=(), min_size=4, max_clusters=12,
                       top_termn=8, stop=(), max_df_ratio=0.4):
    """候选立场簇：把"现有词表没命名、但成簇出现"的表达找出来（**只出建议，不改判定**）。

    实现选择（实测后确定）：**按"词表外的高区分度 n-gram"分组**，而不是相似度聚类。
      · 实测：先用 SimHash 相似度聚类（阈值 8、9 段分桶），8 条"产权/房本"同义表达
        （"电梯装完了产权怎么算"/"房本和产权份额才是重点"…）**一条都没聚上**——
        simhash 对"同义不同字"的表达距离远大于阈值，而放宽阈值会把不相关文本也并进来。
      · 现在改为：统计残差池里 n-gram 的文档频率，取"不在词表里、df ≥ min_size、
        且 df ≤ max_df_ratio×池大小（排除泛词）"的 n-gram 作为种子，把包含该 n-gram 的
        记录归为一簇，并合并成员集合高度重叠的种子。这样簇是有名字（种子词）的、可解释的。
    仍然只出建议：脚本不会自动新增立场、不改任何占比。
    """
    rows = list(rows or [])
    n_pool = len(rows)
    if n_pool < int(min_size):
        return {"method": "term-based (out-of-vocabulary n-gram grouping)", "cluster_count": 0,
                "suspected_unnamed_count": 0, "clusters": [],
                "note": "残差池过小（%d < %d），不做候选簇分析" % (n_pool, int(min_size))}
    vocab = [str(v) for v in (vocab or []) if str(v).strip()]
    st = set(stop or ())
    texts = [normalize_text(str(text_fn(r) or "")) for r in rows]
    df = Counter()
    doc_terms = []
    for t in texts:
        terms = set()
        for n in (3, 4, 5, 6):
            for i in range(max(0, len(t) - n + 1)):
                g = t[i:i + n]
                if len(g) < 3 or g in st:
                    continue
                terms.add(g)
        # **排序后再累加**：set 的迭代顺序随进程的字符串哈希种子变化，
        # 直接遍历 set 会让 df 的并列项顺序不同 → 工件逐位不可复现（实测踩过）。
        doc_terms.append(sorted(terms))
        for g in doc_terms[-1]:
            df[g] += 1
    max_df = max(int(min_size), int(n_pool * float(max_df_ratio)))

    def _known(term):
        return any(term in v or v in term for v in vocab)

    # 种子排序：先按 df 降序，再按词形升序（保证并列项顺序确定）
    seeds = sorted([(g, c) for g, c in df.items()
                    if c >= int(min_size) and c <= max_df and not _known(g)],
                   key=lambda x: (-x[1], x[0]))
    clusters, used_terms = [], []
    for g, c in seeds:
        if any(g in u or u in g for u in used_terms):
            continue
        members = [i for i, terms in enumerate(doc_terms) if g in terms]
        if len(members) < int(min_size):
            continue
        used_terms.append(g)
        authors = sorted({str(rows[i].get("author") or "(未署名)") for i in members})
        # 簇内还出现哪些高分词（给人工定名更多线索）
        inner = Counter()
        for i in members:
            for t in doc_terms[i]:
                if t != g and not _known(t):
                    inner[t] += 1
        clusters.append({
            "seed_term": g, "size": len(members), "authors": len(authors),
            "top_terms": [{"term": t, "df": v} for t, v in sorted(inner.items(),
                                                                  key=lambda x: (-x[1], x[0]))[:int(top_termn)]],
            "known_ratio": 0.0,
            "suspected_unnamed": True,
            "samples": [str(text_fn(rows[i]) or "")[:120] for i in members[:2]],
        })
        if len(clusters) >= int(max_clusters) * 2:
            break
    clusters.sort(key=lambda x: -x["size"])
    return {
        "method": "term-based: out-of-vocabulary n-gram seeds (n=3..6, df≥%d, df≤%d)，"
                  "合并同一簇的重复种子" % (int(min_size), max_df),
        "min_size": int(min_size),
        "pool_size": n_pool,
        "cluster_count": len(clusters),
        "suspected_unnamed_count": sum(1 for x in clusters if x["suspected_unnamed"]),
        "clusters": clusters[:int(max_clusters)],
        "note": "这些是**构造词典的候选**（种子词是词表外的高区分度表达），不是立场判定："
                "脚本不会自动新增立场、不改任何占比。是否入表由人/LLM 判读后决定，"
                "改词表请同步 codebook 并用 vocab.py --diff 留痕。",
    }


def author_stance_distribution(rows, label_fn, other_names=("未识别", "无关/其他")):
    """按"作者主立场"统计的人数分布（可加总到 100%）。

    规则：每位作者取其全部记录中命中数最多的立场（本函数只拿到标签，故按记录数取众数，
    并列时按出现顺序取先者）；无立场记录的作者不计入 —— 于是人数占比之和恰为 100%。
    另同时给出"至少有一条该立场记录的作者数"（可跨立场，故之和可能 >100%），两者并列披露。
    """
    per_author = defaultdict(list)
    for r in rows:
        per_author[str(r.get("author") or "(未署名)")].append(label_fn(r))
    main, any_of = Counter(), Counter()
    for a, labels in per_author.items():
        cnt = Counter(l for l in labels if l and l not in other_names)
        if not cnt:
            continue
        for l in cnt:
            any_of[l] += 1
        top_n = max(cnt.values())
        winners = [l for l, c in cnt.items() if c == top_n]
        main[winners[0]] += 1
    total = sum(main.values())
    return {
        "authors_with_stance": total,
        "authors_total": len(per_author),
        "by_main_stance": [{"stance": s, "authors": c,
                            "pct": round(c / total * 100, 1) if total else 0}
                           for s, c in main.most_common()],
        "any_record_authors": [{"stance": s, "authors": c,
                                "pct": round(c / total * 100, 1) if total else 0}
                               for s, c in any_of.most_common()],
        "note": "by_main_stance 按作者主立场归类，可加总到 100%（与条数占比并排读）；"
                "any_record_authors = 至少有一条该立场记录的作者数，作者可跨立场，故之和可能 >100%。"
                "两者差异就是『同一批人在多个立场间摇摆』的规模。",
    }
