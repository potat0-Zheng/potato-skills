# -*- coding: utf-8 -*-
"""pan-feng-chao 可选增强层探测（零硬依赖、不联网、不安装）。

路线约定（重要，改代码前先读）：
  1. **默认路径只用标准库**。装了可选库也不会自动改变任何数字——必须显式开开关。
     这是"同一语料在任何机器上跑出同一个数字"这条纪律的前提。
  2. 探测只查"在不在"：用 importlib.util.find_spec，不 import 重库，
     避免每次调用都付出加载代价（numpy/transformers 动辄数百毫秒到数秒）。
  3. **缺失即降级，且降级必须被写进工件**（pipeline.degraded），不许静默。
  4. 本文件永不联网、永不安装依赖、永不写用户目录以外的位置。

目前真正被使用的可选增强只有一个：jieba（残差高频词诊断，辅助构造立场词典）。
其余为占位探测，供后续接入时先有披露位。
"""
import importlib.util
import os
import re
from collections import Counter

OPTIONAL = {
    "jieba": {"dist": "jieba", "why": "残差高频词（词级）诊断，辅助词典构造；不参与判定"},
    "sentence_transformers": {"dist": "sentence-transformers", "why": "嵌入聚类（候选立场发现的可选增强）"},
    "numpy": {"dist": "numpy", "why": "嵌入聚类的数值运算辅助（可选）"},
}

_CJK = re.compile(r"[\u4e00-\u9fff]")
_ASCII_DIGIT = re.compile(r"[0-9]")


def _version(dist):
    try:
        from importlib.metadata import version
        return version(dist)
    except Exception:
        return ""


def probe(names=None):
    """探测可选模块是否可用：{模块名: {available, version, why}}。不 import 模块本体。"""
    out = {}
    for name in (names or sorted(OPTIONAL)):
        meta = OPTIONAL.get(name, {})
        try:
            avail = importlib.util.find_spec(name) is not None
        except Exception:
            avail = False
        out[name] = {"available": bool(avail), "version": _version(meta.get("dist", name)) if avail else "",
                     "why": meta.get("why", "")}
    return out


def manifest(requested=(), used=(), extra=None):
    """把"请求了哪些增强 / 哪些真用上了 / 哪些降级了"整理成可写进工件的字典。"""
    requested = [x for x in (requested or []) if x]
    used = [x for x in (used or []) if x]
    probed = probe()
    degraded = []
    for x in requested:
        if x not in probed:
            degraded.append({"name": x, "reason": "未知增强项（不在 enhance.OPTIONAL 内）"})
        elif not probed[x]["available"]:
            degraded.append({"name": x, "reason": "未安装：%s" % probed[x]["why"]})
        elif x not in used:
            degraded.append({"name": x, "reason": "可用但本次未使用"})
    mode = "enhanced" if used else "stdlib"
    return {
        "mode": mode,
        "requested": requested,
        "used": used,
        "degraded": degraded,
        "probe": probed,
        "note": "默认路径只用标准库。mode=enhanced 表示本次额外使用了可选增强，"
                "但增强项**不参与立场判定**，只增加诊断信息；判定口径不随环境变化。",
    }


def jieba_top_terms(texts, topn=60, work_dir="", stop=None):
    """词级高频词（文档频率）诊断，返回 {"available", "terms", "reason"}。

    · 只在装了 jieba 时可用，未装则 available=False 并给出原因（不报错、不影响主流程）。
    · work_dir：jieba 首次运行要写缓存，而本沙箱的默认临时目录可能不可写，
      故把缓存目录显式指到调用方给的可写路径（失败则退回 jieba 默认行为）。
    """
    if importlib.util.find_spec("jieba") is None:
        return {"available": False, "terms": [],
                "reason": "未安装 jieba（pip install jieba 后可用；不影响默认路径与任何数字）"}
    try:
        import jieba  # noqa: PLC0415 —— 可选依赖，必须延迟导入
    except Exception as e:
        return {"available": False, "terms": [], "reason": "jieba 导入失败：%s" % e}
    if work_dir:
        try:
            os.makedirs(work_dir, exist_ok=True)
            jieba.dt.tmp_dir = work_dir                              # 缓存落进可写目录
            jieba.dt.cache_file = os.path.join(work_dir, "jieba.cache")
        except Exception:
            pass
    st = set(stop or ())
    df = Counter()
    try:
        for t in texts:
            seen = set()
            for w in jieba.cut(t or ""):
                w = (w or "").strip()
                if len(w) < 2 or w in st or w in seen:
                    continue
                if not _CJK.search(w) or _ASCII_DIGIT.search(w):
                    continue
                seen.add(w)
            for w in seen:
                df[w] += 1
    except Exception as e:
        return {"available": True, "terms": [], "reason": "jieba 分词失败：%s" % e}
    return {"available": True, "terms": [{"word": w, "df": c} for w, c in df.most_common(int(topn))],
            "reason": ""}


def embed_clusters(texts, min_size=4, cosine=0.75, max_texts=2000, model="shibing624/text2vec-base-chinese"):
    """嵌入聚类（**可选增强**）：发现"语义相近但字面不同"的候选簇。

    用途与 structure.candidate_clusters 相同（发现未命名立场），区别是它按语义而非字面相似：
    字面法漏掉的"换了说法讲同一件事"由它补上。原则不变——**只出候选，不改判定、不改占比**。

    · 未装 sentence-transformers → available=False 并给出原因（不报错、不影响主流程）；
    · 装了也只在显式开启（--candidates embed）时运行，且结果只写进 pipeline.optional_output；
    · 纯 Python 贪心聚类（cosine ≥ 阈值），故限制 max_texts 上限，避免长跑；
    · 本函数的"已安装"分支未在本机验证过（本机未装 sentence-transformers），
      故按"可能失败即降级"的写法处理：任何异常都返回 available=False + 原因。
    """
    if importlib.util.find_spec("sentence_transformers") is None:
        return {"available": False, "clusters": [], "reason":
                "未安装 sentence-transformers（pip install sentence-transformers 后可用；"
                "不影响默认路径、候选簇默认走字面法 structure.candidate_clusters）"}
    items = [t for t in (texts or []) if str(t or "").strip()][:int(max_texts)]
    if len(items) < int(min_size):
        return {"available": True, "clusters": [], "reason": "文本数不足 %d 条，不做嵌入聚类" % min_size}
    try:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415
        model_obj = SentenceTransformer(model)
        vecs = model_obj.encode(items, normalize_embeddings=True, show_progress_bar=False)
    except Exception as e:
        return {"available": False, "clusters": [], "reason": "嵌入模型加载/编码失败：%s" % e}
    try:
        clusters, used = [], set()
        for i in range(len(items)):
            if i in used:
                continue
            group = [i]
            for j in range(i + 1, len(items)):
                if j in used:
                    continue
                s = sum(float(a) * float(b) for a, b in zip(vecs[i], vecs[j]))
                if s >= float(cosine):
                    group.append(j)
            if len(group) >= int(min_size):
                used.update(group)
                clusters.append({"size": len(group), "samples": [items[k][:120] for k in group[:3]]})
    except Exception as e:
        return {"available": False, "clusters": [], "reason": "嵌入聚类失败：%s" % e}
    clusters.sort(key=lambda x: -x["size"])
    return {"available": True, "clusters": clusters[:12], "cosine": float(cosine),
            "n_texts": len(items), "reason": "",
            "note": "仅候选：需人工/LLM 判读后才能写进 stances.json；本层不参与任何占比计算。"}
