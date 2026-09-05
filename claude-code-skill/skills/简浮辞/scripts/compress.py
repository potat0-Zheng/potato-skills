#!/usr/bin/env python3
"""简浮辞 — 中文文本语义压缩。

七层流水线（简化实现）:
  1. 文本预处理（分句/分段）
  2. jieba 分词 + 词性标注
  3. 实体识别（基于 jieba 词性 nr/ns/nt/nz）
  4. 删除权重计算 & 结构化剪枝
  5. 规则拼接重建
  6. 语义相似度校验（可选，需 sentence-transformers，默认关闭）
  7. 全局连贯性修复

依赖: pip install jieba
可选: pip install sentence-transformers  (仅 --check)
"""

import re
import json
import sys
import os
import argparse

# ── 预编译正则 ──────────────────────────────────────────────────────
# 中文句子边界（。！？；换行分隔）
SENT_SPLIT_RE = re.compile(r"[。！？；\n]+")
# 保留字符（不在分词结果中，但需留在输出中）
KEEP_CHARS = set("，。！？；：、""''（）《》【】…—·")
# 冗余括号对（中文引号变体）
REDUNDANT_PAIRS = [
    ("“", "”"), ("‘", "’"), ("（", "）"), ("《", "》"),
    ("【", "】"), ("「", "」"), ("『", "』"),
]
# 英文/数字/符号块，不应被切除
LATIN_BLOCK = re.compile(r"[a-zA-Z0-9À-ɏ@#$%&*+\\/=<>~^|]+")

# ── jieba 初始化 ─────────────────────────────────────────────────────
try:
    import jieba
    import jieba.posseg as pseg
except ImportError:
    sys.exit("需要 jieba: pip install jieba")

rule_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules.json")
try:
    with open(rule_path, "r", encoding="utf-8") as f:
        rules = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    sys.exit(f"无法读取规则文件: {rule_path}")

# ── 预计算 lookups ───────────────────────────────────────────────────
ALWAYS_KEEP = set(rules["pos_keep"]["always"].keys())
CONTEXTUAL = set(rules["pos_keep"]["contextual"].keys())
ALWAYS_REMOVE = set(rules["pos_remove"]["always"].keys())
KEEP_WORDS = set(rules["keep_exceptions"]["words"])
REMOVE_WORDS = set(rules["remove_exceptions"]["words"])

# ── 删除词列表 ───────────────────────────────────────────────────────
REMOVE_INTENSIFIERS = {
    "很", "非常", "十分", "极其", "相当", "特别", "格外", "太",
    "颇", "甚", "挺", "满", "好", "多么", "何等", "极度", "尤为",
}
REMOVE_PARTICLES = {
    "的", "了", "着", "过", "地", "得", "之", "所",
}
REMOVE_CONJUNCTIONS = {
    "然后", "接着", "并且", "以及", "而且", "况且", "此外",
    "另外", "同时", "与此同时", "另一方面",
    "其实", "当然", "显然", "的确", "确实",
    "总之", "总而言之", "综上所述",
}
REMOVE_PRONOUNS = {
    "我", "我们", "你", "您", "你们", "他", "她", "它",
    "他们", "她们", "它们", "俺", "咱", "咱们",
    "本人", "自身",
}
# 冗余动词（形式动词，可用核心动词替代）
REMOVE_DUMMY_VERBS = {
    "进行", "给予", "加以", "予以", "作出", "使得",
}
# 冗余量词头
REMOVE_CLASSIFIERS = {
    "一个", "一种", "某个", "某些", "该", "本", "此", "这个", "那个",
    "这些", "那些",
}

# ── Layer 1: 分句 ───────────────────────────────────────────────────
def split_sentences(text):
    """将文本按句末标点和换行分割，保留分隔符信息。"""
    # 先用换行分段
    paragraphs = text.split("\n")
    sentences = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            sentences.append("")  # 空行作为段落分隔
            continue
        # 按句末标点分割，但保留分隔符
        parts = re.split(r"([。！？；])", para)
        cur = ""
        for part in parts:
            if part in "。！？；":
                cur += part
                if cur.strip():
                    sentences.append(cur.strip())
                cur = ""
            else:
                cur += part
        if cur.strip():
            sentences.append(cur.strip())
    return sentences


# ── Layer 2: jieba 分词 + 词性标注 ───────────────────────────────────
def tokenize(text):
    """返回 [(word, pos), ...]"""
    tokens = []
    for w, f in pseg.cut(text):
        tokens.append((w, f))
    return tokens


# ── Layer 3: 实体识别（基于词性，不依赖 HanLP）──────────────────────
def is_entity(pos):
    """判断词性是否为命名实体。"""
    return pos in ("nr", "ns", "nt", "nz")


# ── Layer 4: 删除权重计算 & 剪枝 ────────────────────────────────────
def should_keep(word, pos, preset="medium", prev_word=None, next_word=None):
    """
    判断一个词是否保留。
    决策优先级:
      1. 显式例外词（keep_exceptions / remove_exceptions）
      2. POS 分类（ALWAYS_KEEP > CONTEXTUAL > ALWAYS_REMOVE）
      3. Preset 特定规则（intensifiers, particles, conjunctions, pronouns）
    """
    cfg = rules["compression_presets"].get(preset, rules["compression_presets"]["medium"])

    # ── 显式例外 ──
    if word in KEEP_WORDS:
        return True, "keep_exception"
    if word in REMOVE_WORDS:
        return False, "remove_exception"

    # ── 拉丁/数字块保留 ──
    if LATIN_BLOCK.fullmatch(word):
        return True, "latin_block"

    # ── POS 决策 ──
    if pos in ALWAYS_KEEP:
        return True, f"pos:{pos}"
    # 标点
    if pos == "x" and word not in KEEP_CHARS:
        return word in KEEP_CHARS, "punct"
    if pos in ALWAYS_REMOVE:
        return False, f"pos:{pos}"

    # ── Contextual POS ──
    if pos == "d":  # 副词
        if word in ("不", "没", "无", "未", "非", "别", "莫", "甭", "勿", "休"):
            return True, "negation"
        if any(word.startswith(kw) for kw in ("必须", "应该", "一定", "可能", "也许", "似乎", "好像", "大概", "大约", "至少", "最多", "最少")):
            return True, "modal_quantifier"
        if cfg["remove_intensifiers"] and word in REMOVE_INTENSIFIERS:
            return False, "intensifier"
        if word in ("就", "才", "只", "光", "仅", "单单"):
            return True, "scope_adverb"
        if word in ("已", "已经", "曾经", "正在", "将", "将要"):
            return True, "aspect_adverb"
        if word in ("也", "还", "又", "再", "更", "越"):
            return True, "additive_adverb"
        return False, "default_adverb"

    if pos == "a":  # 形容词
        # 单字形容词常作为修饰（保留）、双字及以上更可能是独立语义
        if len(word) == 1:
            return False, "mono_adj"
        return True, "adj"

    if pos == "p":  # 介词
        if word in ("在", "从", "到", "于", "自", "朝", "向", "往", "沿", "临"):
            return True, "spatial_prep"
        if word in ("以", "用", "靠", "通过", "经过", "根据", "按照", "凭"):
            return True, "instrumental_prep"
        if word in ("因为", "由于", "为了", "为"):
            return True, "causal_prep"
        # 语法工具介词（被、把、将、对、对于、关于、给、让）→ 视 preset
        return False, "grammar_prep"

    if pos == "c":  # 连词
        if word in ("因为", "所以", "因此", "由于", "如果", "假如", "要是", "即使", "尽管", "虽然", "但是", "但", "然而", "却"):
            return True, "logic_conj"
        if cfg["remove_conjunctions"]:
            return False, "sequence_conj"
        return True, "conj"

    if pos == "r":  # 代词
        if word in ("什么", "怎么", "哪", "哪儿", "哪里", "谁", "何", "如何", "为何", "这", "那", "这样", "那样"):
            return True, "interrogative_demo"
        if word in ("自己", "自身", "彼此", "互相", "各自"):
            return True, "reflexive"
        if cfg["remove_pronouns"]:
            return False, "pronoun"
        return True, "pronoun"

    if pos == "u":  # 助词
        if cfg["remove_particles"]:
            if word == "的":
                # 的 字保留条件：前词是代词/名词且表所有格 → "我的书" vs "蓝色的天"
                if prev_word and any(kw in word for kw in ("我", "你", "他", "她", "它", "我们", "你们", "他们", "自己", "谁")):
                    return True, "possessive_de"
                return False, "de"
            return False, f"particle_{word}"
        return True, "particle_kept"

    # ── 默认保留 ──
    return True, "default"


def prune(tokens, preset="medium"):
    """返回 [(word, keep_bool, reason), ...]"""
    decisions = []
    for i, (word, pos) in enumerate(tokens):
        prev_word = tokens[i - 1][0] if i > 0 else None
        next_word = tokens[i + 1][0] if i < len(tokens) - 1 else None
        keep, reason = should_keep(word, pos, preset, prev_word, next_word)
        decisions.append((word, keep, reason))
    return decisions


# ── Layer 5: 规则拼接重建 ──────────────────────────────────────────
def reconstruct(decisions):
    """基于剪枝结果重建文本，处理标点粘连。"""
    result = []
    prev_removed = False

    for i, (word, keep, reason) in enumerate(decisions):
        if not keep:
            prev_removed = True
            continue

        # 标点特殊处理
        if word in KEEP_CHARS:
            # 删除前一个词后多余的标点
            if prev_removed and word in "，,、；;：:。！？":
                # 如果前面被跳过的是标点，同时去掉多余的连接标点
                prev_removed = False
                if result and result[-1] in "。！？；\n":
                    continue  # 句末标点后不要再加逗号
                continue
            result.append(word)
            prev_removed = False
            continue

        # 处理词间空格
        if LATIN_BLOCK.fullmatch(word):
            # 拉丁词/数字，前后加空格
            if result and not result[-1].isspace() and result[-1] not in KEEP_CHARS:
                result.append(" ")
            result.append(word)
            # 不是最后一个且下一个不是标点
            if i + 1 < len(decisions):
                next_w = decisions[i + 1][0]
                if next_w not in KEEP_CHARS and not LATIN_BLOCK.fullmatch(next_w):
                    result.append(" ")
        else:
            result.append(word)

        prev_removed = False

    return "".join(result)


# ── Layer 6: 语义相似度校验（可选）───────────────────────────────────
def semantic_check(original, compressed, threshold=0.85):
    """使用 sentence-transformers 计算语义相似度。仅在 --check 时调用。"""
    try:
        from sentence_transformers import SentenceTransformer, util
    except ImportError:
        print("[警告] sentence-transformers 未安装，跳过语义校验。pip install sentence-transformers")
        return None

    model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
    emb = model.encode([original, compressed])
    sim = float(util.cos_sim(emb[0], emb[1])[0][0])
    return {"similarity": sim, "pass": sim >= threshold}


# ── Layer 7: 全局连贯性修复 ────────────────────────────────────────
def coherence_repair(sentences):
    """修复压缩后句间的冗余重复和指代断裂。"""
    if len(sentences) <= 1:
        return sentences

    repaired = []
    prev = ""
    for s in sentences:
        # 去空串
        s = s.strip()
        if not s:
            if repaired and repaired[-1] != "":
                repaired.append("")  # 保留段落分隔
            continue
        # 去与上句完全重复的开头（如连词堆叠）
        if prev and s == prev:
            continue
        # 连续逗号修复
        s = re.sub(r"，{2,}", "，", s)
        s = re.sub(r"。{2,}", "。", s)
        repaired.append(s)
        prev = s
    return repaired


# ── 统计 ─────────────────────────────────────────────────────────────
def stats(original, compressed):
    orig_chars = len(original.replace("\n", "").replace(" ", ""))
    comp_chars = len(compressed.replace("\n", "").replace(" ", ""))
    ratio = comp_chars / orig_chars if orig_chars > 0 else 1.0
    return {
        "original_chars": orig_chars,
        "compressed_chars": comp_chars,
        "ratio": round(ratio, 3),
        "reduction": f"{round((1 - ratio) * 100, 1)}%",
    }


# ── Main ─────────────────────────────────────────────────────────────
def compress(text, preset="medium", check=False):
    """主压缩函数。返回 (compressed_text, stats_dict, check_result_or_none)"""
    sentences = split_sentences(text)
    compressed_sentences = []

    for sent in sentences:
        if not sent.strip():
            compressed_sentences.append("")
            continue
        tokens = tokenize(sent)
        decisions = prune(tokens, preset)
        compressed = reconstruct(decisions)
        compressed_sentences.append(compressed)

    repaired = coherence_repair(compressed_sentences)
    result = "\n".join(repaired)

    # 清理多余空行
    result = re.sub(r"\n{3,}", "\n\n", result)

    check_result = None
    if check:
        clean_text = text.replace("\n\n", "\n")
        clean_result = result.replace("\n\n", "\n")
        check_result = semantic_check(clean_text, clean_result)

    return result, stats(text, result), check_result


def main():
    ap = argparse.ArgumentParser(description="简浮辞 — 中文语义压缩")
    ap.add_argument("input", help="输入文件路径，或 - 读 stdin")
    ap.add_argument("-o", "--output", help="输出文件路径，默认 stdout")
    ap.add_argument("-p", "--preset", choices=["light", "medium", "heavy"], default="medium",
                    help="压缩等级 (default: medium)")
    ap.add_argument("-c", "--check", action="store_true",
                    help="启用语义相似度校验（需 sentence-transformers）")
    ap.add_argument("-s", "--stats-only", action="store_true",
                    help="仅输出统计信息，不输出文本")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="显示逐词决策详情")
    args = ap.parse_args()

    # 读输入
    if args.input == "-":
        text = sys.stdin.read()
    else:
        with open(args.input, "r", encoding="utf-8") as f:
            text = f.read()

    if not text.strip():
        print("输入文本为空")
        sys.exit(0)

    # 压缩
    result, st, check_result = compress(text, args.preset, args.check)

    if args.stats_only:
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return

    # 输出
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result)
    else:
        print(result)

    # 统计到 stderr
    level = {"light": "轻度", "medium": "中度", "heavy": "重度"}
    print(f"\n[简浮辞/{level.get(args.preset, args.preset)}] "
          f"{st['original_chars']} → {st['compressed_chars']} 字符 "
          f"(压缩 {st['reduction']})",
          file=sys.stderr)

    if check_result:
        status = "✓ 通过" if check_result["pass"] else "⚠ 低于阈值"
        print(f"[语义相似度] {check_result['similarity']:.3f} {status} (阈值 0.85)",
              file=sys.stderr)

    if args.verbose:
        sentences = split_sentences(text)
        for sent in sentences:
            if not sent.strip():
                continue
            tokens = tokenize(sent)
            decisions = prune(tokens, args.preset)
            for word, keep, reason in decisions:
                mark = "✓" if keep else "✗"
                print(f"  {mark} {word}\t{reason}", file=sys.stderr)
            print(file=sys.stderr)


if __name__ == "__main__":
    main()
