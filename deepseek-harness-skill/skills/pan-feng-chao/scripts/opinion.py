# -*- coding: utf-8 -*-
"""pan-feng-chao 舆论立场聚类（v0.5，四层口径）。

读治理后的 JSONL（最小字段要求见 SKILL.md），按事件定制的立场词典打标
（优先 --stances 文件，缺省读取本 skill config.json 的 stance_keywords；两者皆空时报错退出）。

四层口径：**以 ~/.dsh/report-theme/CONTRACT-joint.md §9.1 为唯一权威，本文件不复制口径文本**，
只在此列出实现所对应的层名，便于对读：
  A 个人表达样本   —— 立场占比的分母
  B 机构/媒体帖    —— 仅按账号名机构特征词判定（v0.4 已删「报道用语 + 话题标签」规则）；识别下界，不进分母
  C 未识别残差     —— 词典未命中者（个人 UGC），并输出三段构成诊断
  D 治理剔除噪声   —— 仅计数（需 --total-raw；未传时为 null 而**不是 0**）

为什么把 B/C 摘出去：机构帖是信息供给方而非舆论主体，把「报道了某立场」当成「持某立场」，
会让高赞代表被媒体转述帖占满；把「未识别」当成一个立场画进占比，则是把方法缺陷当数据结论。

v0.5 变更（对应写入计划 §4 的 D 系列）：
  · D1 机构判定收窄为「尾部成词后缀 + 长名成词 + 匿名化正则」，删掉单字泛词。旧规则把
    13 个普通昵称中的 10 个判成机构帖（网友A/网名不好起/阿闻/视频博主小李/报料人/都市丽人/
    发布君/观察者小王/资讯控/频道主），把真实舆论主体踢出分母，方向与契约要求的
    「宁可偏小（保守），不可偏大（污染分母）」相反。
  · D2 stance_timeline 无条件统计且**只含 A 层**（旧实现写在 `if rules` 块内：不给 --rules 就
    恒为 {}，报告的堆叠条与趋势折线整体消失；且把「未识别」也写成键，某天只有未识别时被画成空白行）。
  · D3 删除 --no-split（该开关能一键产出契约禁止的形态，而门禁全是文本串检查，拦不住）。
  · D4 --rules 只认显式传入（旧实现自动加载 --out 同目录的 opinion_rules.json，
    使「无规则层」的对照实验其实带着规则层，排障时系统性误判）。
  · D9 新增 coverage_of_judgeable_norule：契约口径的可判集合含规则层独有项，同一语料
    「跑/不跑规则层」会给出两个覆盖率（实测 50.0% / 33.3%），跨报告不可比。
  · D12 --total-raw 缺省输出 null（旧实现把「未知」写成 0，读者会读成「未剔除」）。
  · D14 阈值真读 config.json（命令行 > config > 内置默认）。
  · 新增：占比 Wilson 置信区间、分层估计工具（stats.py）、外部 LLM 标注层（导入式，不联网）、
    可选增强探测与降级披露（enhance.py，**不改变默认路径的任何数字**）。

产物：opinion.json / opinion.md。**字段名与旧版兼容，只增不改**——百宝袋装配端与门禁无需改动。

用法：
  python opinion.py --in data/{event}/normalized/data.relevant.jsonl \
      --out data/{event}/opinion.json --event "事件关键词" --stances data/{event}/stances.json
"""
import argparse
import datetime
import json
import os
import re
import sys
from collections import Counter, defaultdict

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import enhance  # noqa: E402  同目录共用模块：可选增强探测（零硬依赖）
import manifest as manifest_mod  # noqa: E402  同目录共用模块：工件指纹
import stats    # noqa: E402  同目录共用模块：纯标准库统计
import structure  # noqa: E402  同目录共用模块：声量/人数/重复结构（纯标准库）

# 立场词典按事件定制（--stances），不设事件性兜底默认；
# 词典为空时 main() 报错退出，防止静默错误归类。
FALLBACK_STANCE = {}
OTHER = "未识别"

# 机构/媒体识别（启发式，识别下界：平台常对账号名做匿名化，如「潇***报」）
# v0.4：删掉「报道用语 + 话题标签」这条规则——话题标签网友也用得极多，
#       该规则会把普通网友长帖误判为机构帖、从分母里拿走真实舆情。宁可少判（保守），不可误判（污染分母）。
# v0.5（D1）：删掉单字/泛词（网/闻/报/频/发布/观察/资讯/都市），改为「尾部后缀 + 长名成词 + 匿名化正则」。
#   实测旧规则把 13 个普通昵称中的 10 个判成机构帖（网友A/网名不好起/阿闻/视频博主小李/报料人/
#   都市丽人/发布君/观察者小王/资讯控/频道主），等于把真实舆论主体从分母里拿走。
#   单字后缀（网/报/台/社/刊）改为**必须位于账号名尾部**且账号名 ≥3 字，故「报料人」不再命中。
#   同时补匿名化形态：`潇***报` 不匹配任何成词后缀，只做成词匹配会把「误判过多」翻成「全部漏检」。
DEFAULT_INSTITUTIONAL = {
    # 命中即判：必须位于账号名**尾部**
    "name_suffixes": ["日报", "晚报", "时报", "晨报", "快报", "商报", "周报", "新闻网", "新闻中心",
                      "通讯社", "广播电台", "电视台", "融媒体", "客户端", "发布厅", "观察者网",
                      "共青团", "妇联", "新闻", "广电", "网", "报", "台", "社", "刊"],
    # 可出现在账号名中段，但要求账号名长度 ≥ 该词的最小长度（成词，避免「都市丽人」这类误判）
    "name_words": [["日报", 4], ["晚报", 4], ["时报", 4], ["晨报", 4], ["快报", 4], ["商报", 4],
                   ["周报", 4], ["新闻网", 4], ["新闻中心", 4], ["通讯社", 4], ["广播电台", 4],
                   ["电视台", 4], ["融媒体", 4], ["客户端", 4], ["发布厅", 4], ["观察者网", 4],
                   ["共青团", 4], ["妇联", 4]],
    # 匿名化与自定义形态（也接受 re: 前缀写进 name_suffixes）
    "name_regexes": [r"^\*+$", r"\*{1,}(报|网|台|社|刊|新闻)$",
                     r"^[\u4e00-\u9fff]{1,4}\*+(报|网|台|社|刊)$"],
    # 旧版正文规则：v0.4 起停用；保留键位以免旧 --institutional 文件报错
    "repost_patterns": [],
    "name_min_len_for_single": 3,
    "note": "仅按账号名机构特征词判定（启发式下界）。name_suffixes=尾部后缀（单字后缀另要求账号名 ≥3 字），"
            "name_words=长名成词（低于要求长度不判），name_regexes=匿名化形态；"
            "repost_patterns 空即不启用正文规则（v0.4）。B 层占比 >15% 会告警——真实事件语料里机构帖通常是小少数。",
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


# ---- config.json：阈值的集中处（v0.5，D14）----
# 优先级：命令行 > config.json > 内置默认。旧实现把少数派阈值硬编码在代码里、把 70% 只写在
# SKILL.md 散文里，于是配置与实现两张皮——改配置不生效，改代码没人知道。
CONFIG_DEFAULTS = {
    "coverage_target": 70,      # 覆盖率目标（百分点），低于它只告警，不改数字
    "minority_threshold": 10,   # 少数派判定阈值（百分点）
    "mid_samples": 12,          # 残差中位段抽样条数
    "stance_keywords": {},      # 立场词典兜底（恒为空，词典须按事件定制）
    "stance_note": "",
    # ---- v0.7 新增（E4.5 / E5.3）：措辞闸门与漂移告警的阈值，避免又一处硬编码 ----
    "trend_min_days": 3,                 # 谈走势所需的最少有效日数
    "trend_min_day_denominator": 20,     # 单日至少这么多条 A 层记录才算"有效日"
    "drift_coverage_pp": 5,              # 覆盖率变化超过该百分点即告警
    "drift_denominator_pct": 10,         # 立场分母变化超过该百分比即告警
}


def load_config():
    """读本 skill 的 config.json。缺失/损坏时退回内置默认，并**在产物中披露来源**。"""
    cfg = dict(CONFIG_DEFAULTS)
    cfg["_source"] = "builtin_default"
    cfg["_path"] = os.path.join(SKILL_DIR, "config.json")
    cfg["_from_config"] = []
    if os.path.exists(cfg["_path"]):
        try:
            with open(cfg["_path"], "r", encoding="utf-8") as f:
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


def load_stances(path=""):
    """加载立场词典：优先 --stances 指定的文件；否则用 config.json 的 stance_keywords。"""
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                return _clean_stances(json.load(f))
        except Exception as e:
            print("[opinion] 警告：--stances 读取失败（%s），回退 config.json" % e)
    return _clean_stances(load_config().get("stance_keywords", {}))


def load_institutional(path=""):
    """加载机构识别规则；缺省用内置 DEFAULT_INSTITUTIONAL。

    自定义文件的键与内置**合并**（不是替换）：name_suffixes / name_words / name_regexes；
    兼容旧键 name_patterns（按后缀语义并入，并在账号名过长时给出提示）。
    repost_patterns（正文规则）v0.4 起停用，显式传入仍会执行，但会打印披露提示。
    """
    cfg = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_INSTITUTIONAL.items()}
    if not (path and os.path.exists(path)):
        return cfg
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            user = json.load(f)
    except Exception as e:
        print("[opinion] 警告：--institutional 读取失败（%s），使用内置规则" % e)
        return cfg
    if not isinstance(user, dict):
        return cfg
    merged = []
    if isinstance(user.get("name_suffixes"), list):
        merged += [str(x) for x in user["name_suffixes"] if str(x).strip()]
    if isinstance(user.get("name_patterns"), list) and user["name_patterns"]:
        merged += [str(x) for x in user["name_patterns"] if str(x).strip()]
        print("[opinion] 提示：--institutional 使用了旧键 name_patterns，已按「尾部后缀」语义合并。"
              "单字模式只在账号名尾部命中（v0.5 规则），若原意是中段匹配请改用 name_words。")
    if merged:
        cfg["name_suffixes"] = list(cfg.get("name_suffixes") or []) + merged
    if isinstance(user.get("name_words"), list) and user["name_words"]:
        add = []
        for x in user["name_words"]:
            if isinstance(x, (list, tuple)) and len(x) == 2:
                add.append([str(x[0]), int(x[1])])
            elif str(x).strip():
                add.append([str(x), 4])
        cfg["name_words"] = list(cfg.get("name_words") or []) + add
    if isinstance(user.get("name_regexes"), list) and user["name_regexes"]:
        cfg["name_regexes"] = list(cfg.get("name_regexes") or []) + [str(x) for x in user["name_regexes"]]
    if isinstance(user.get("repost_patterns"), list) and user["repost_patterns"]:
        cfg["repost_patterns"] = [str(x) for x in user["repost_patterns"]]
        print("[opinion] 提示：--institutional 传入了 repost_patterns（正文规则）。该规则 v0.4 起停用"
              "（话题标签网友也用得极多），本次仍会执行，请在报告中披露。")
    if isinstance(user.get("note"), str) and user["note"]:
        cfg["note"] = str(user["note"])
    return cfg


_NEG_PREFIX_RE = re.compile(r"[不没无未别莫非](?:是|并|曾|会|能|再)?$")

# E3.5：反讽/否定的多线索（每条线索独立记录，供人工复核与确定度评分；**不自动改判**）
_IRONY_PATTERNS = [
    ("引号包裹", r"[“\"「][^”\"」]{2,40}[”\"」]"),
    ("反问句式", r"(难道|难不成|该不会|莫不是).{0,20}(吗|吧|呢|？|\?)"),
    ("嘲讽用词", r"(呵呵|哈哈|笑死|emmm|醉了|服了|无语|就这|典)"),
    ("夸张标点", r"[!！]{2,}|[?？]{2,}"),
    ("转折反转", r"(不过|但是|然而).{0,12}(倒|倒是|也好|罢了)"),
]


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
    """机构/媒体帖判定。返回 (bool, 理由)。

    v0.5 判定顺序（任何一条命中即判；理由写进 institutional_layer.reasons 供复核）：
      1) name_regexes：匿名化形态（`潇***报`）与自定义正则
      2) name_suffixes：成词后缀且位于账号名**尾部**（单字后缀另要求账号名 ≥ name_min_len_for_single）
      3) name_words：成词命中，且账号名长度 ≥ 该词要求的最小长度
      4) repost_patterns：正文规则（v0.4 起默认停用，仅当调用方显式传入时才走）
    """
    au = str(rec.get("author") or "").strip()
    txt = rec.get("content") or ""
    for pat in cfg.get("name_regexes") or []:
        try:
            if re.search(str(pat), au):
                return True, "账号名匿名化/自定义形态"
        except re.error:
            continue
    min1 = int(cfg.get("name_min_len_for_single") or 3)
    for suf in cfg.get("name_suffixes") or []:
        suf = str(suf)
        if not suf:
            continue
        if suf.startswith("re:"):
            try:
                if re.search(suf[3:], au):
                    return True, "账号名自定义正则"
            except re.error:
                continue
        elif len(suf) == 1:
            if len(au) >= min1 and au.endswith(suf):
                return True, "账号名机构后缀(%s)" % suf
        elif au.endswith(suf):
            return True, "账号名机构后缀(%s)" % suf
    for item in cfg.get("name_words") or []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            w, need = str(item[0]), int(item[1])
        else:
            w, need = str(item), 4
        if w and len(au) >= need and w in au:
            return True, "账号名机构成词(%s)" % w
    for pat in cfg.get("repost_patterns") or []:
        try:
            m = re.search(pat, txt)
        except re.error:
            m = None
        if m and ("【" in txt or "#" in txt):
            return True, "报道用语+话题标签(已停用规则)"
    return False, ""


def load_llm_labels(path=""):
    """导入外部 LLM 标注（层化架构的 llm 层，v0.5）。

    格式：JSONL，或 JSON 数组，或 {"records": [...]}；每项 {"platform","id","stance"[,"confidence"]}。
    **脚本永不联网、永不调用模型**：标注由外部流程产出，这里只做导入与并列统计，
    与词典层/规则层一样不合并、不改分母，且同样在人工抽检前只能作为上界读。
    返回 ({(platform,id): stance}, 读取条数, {(platform,id): confidence})。
    """
    if not path:
        return {}, 0, {}
    if not os.path.exists(path):
        print("[opinion] 警告：--llm-labels 文件不存在（%s），本次不出 llm 层" % path)
        return {}, 0, {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            text = f.read().strip()
        rows = []
        # 先按整体 JSON 解析；失败再按 JSONL 逐行解析（JSONL 的首行也以 { 开头，
        # 不能靠首字符判断格式——这是实测踩过的坑）。
        try:
            obj = json.loads(text)
        except Exception:
            obj = None
        if isinstance(obj, list):
            rows = obj
        elif isinstance(obj, dict):
            rows = obj["records"] if isinstance(obj.get("records"), list) else [obj]
        else:
            rows = [json.loads(l) for l in text.splitlines() if l.strip()]
    except Exception as e:
        print("[opinion] 警告：--llm-labels 解析失败（%s），本次不出 llm 层" % e)
        return {}, 0, {}
    out = {}
    conf = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        st = str(r.get("stance") or "").strip()
        if not st or st == OTHER:
            continue
        k = (r.get("platform"), str(r.get("id") or ""))
        out[k] = st
        if r.get("confidence") is not None:
            try:
                conf[k] = float(r["confidence"])
            except (TypeError, ValueError):
                pass
    return out, len(rows), conf


def digest(path):
    """一屏摘要（≤25 行）：**agent 应该用这个读工件，而不是整体 read JSON**。

    工件会随语料增长（真实事件可达上百 KB），整读会把上下文吃掉大半；
    这里只取结论层字段，明细一律不打印（要明细用 opinion.md 或按字段查）。
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            d = json.load(f)
    except Exception as e:
        print("[opinion] --digest 读取失败（%s）：%s" % (path, e))
        return 2
    kb = os.path.getsize(path) / 1024.0

    def _p(x):
        """缺失值渲染成「—」：旧 schema 的工件缺字段时不能把 None 当成一个数值印出来。"""
        return "—" if x is None else "%s%%" % x

    dist = d.get("stance_distribution") or []
    res = d.get("residual") or {}
    inst = d.get("institutional_layer") or {}
    thr = d.get("thresholds") or {}
    sc = d.get("spotcheck") or {}
    L = ["[opinion --digest] 事件：%s ｜ 语料 %s 条 ｜ 工件 %.1f KB"
         % (d.get("event") or "(未命名)", d.get("total_records"), kb),
         "四层：A %s · B %s · C %s · D %s（null=未知）"
         % (d.get("denominator"), inst.get("count"), res.get("count"),
            (res.get("split") or {}).get("语料治理阶段剔除的无关噪声")),
         "覆盖率：占全量 %s ｜ 契约可判集合 %s ｜ 跨报告可比 %s ｜ 目标 %s"
         % (_p(d.get("coverage_rate")), _p(d.get("coverage_of_judgeable")),
            _p(d.get("coverage_of_judgeable_norule")), _p(thr.get("coverage_target"))),
         "立场（条数 / 占比 / 95%CI / 独立作者 / 少数派）："]
    for x in dist[:12]:
        _ci = x.get("pct_ci95")
        L.append("  · %s：%s 条 %s [%s] %s 人%s"
                 % (x.get("stance"), x.get("count"), _p(x.get("pct")),
                    "–".join(str(v) for v in _ci) if _ci else "—",
                    x.get("author_count") if x.get("author_count") is not None else "—",
                    " [少数派]" if x.get("minority") else ""))
    if len(dist) > 12:
        L.append("  · … 另有 %d 个立场（见 opinion.md）" % (len(dist) - 12))
    for c in (d.get("stance_comparisons") or [])[:2]:
        L.append("差异：%s vs %s %+.1fpp [%s] → %s"
                 % (c["pair"][0], c["pair"][1], c.get("diff_pp", 0),
                    "–".join(str(v) for v in (c.get("ci95_pp") or ["?", "?"])),
                    c.get("verdict", "")))
    pr = d.get("precision") or {}
    if pr.get("half_width_pp_at_p50") is not None:
        L.append("精度：分母 %s → 占比 50%% 处 ±%.1fpp；±10pp 需分母约 %s 条"
                 % (pr.get("denominator"), pr["half_width_pp_at_p50"],
                    (pr.get("required_n_for_margin") or {}).get("±10pp")))
    a = ((d.get("audience_structure") or {}).get("layers") or {}).get("A_个人表达") or {}
    if a:
        dup = (d.get("audience_structure") or {}).get("duplication") or {}
        L.append("声量/人数：%s 条 / %s 人（条·人比 %s）｜HHI %s｜最活跃账号占 %s%% 记录｜高频账号 %s 个"
                 % (a.get("records"), a.get("unique_authors"), a.get("posts_per_author"),
                    (a.get("concentration") or {}).get("hhi_x10000"),
                    (a.get("concentration") or {}).get("top1_share_pct"),
                    (a.get("heavy_accounts") or {}).get("accounts")))
        if dup:
            L.append("同源文本：%s 条（%s%%）｜跨账号 %s · 同账号重复 %s｜规范化同文 %s"
                     % (dup.get("near_duplicate_records"), dup.get("near_duplicate_pct"),
                        dup.get("cross_account_dup_records"), dup.get("same_author_repeat_records"),
                        dup.get("exact_duplicate_records")))
    if inst.get("pct_of_total") is not None:
        L.append("机构帖：%s 条（%s%%）%s"
                 % (inst.get("count"), inst.get("pct_of_total"),
                    "⚠ 超 15% 告警线，需人工确认判定是否过宽"
                    if (inst.get("pct_of_total") or 0) > 15 else ""))
    L.append("层：词典(dict)%s%s%s"
             % (" · 规则(rule %s 条)" % (d.get("rule_layer") or {}).get("count")
                if d.get("rule_layer") else "",
                " · LLM(%s 条，未抽检)" % (d.get("llm_layer") or {}).get("count")
                if d.get("llm_layer") else "",
                " ｜ 管道 %s" % ((d.get("pipeline") or {}).get("mode") or "?")))
    # v0.7：新增闸门与自查一行读完
    _sc = d.get("selfcheck") or {}
    if _sc:
        L.append("走势闸门：%s（有效日 %s / 跨度 %s 天）%s"
                 % ("禁止谈走势" if _sc.get("trend_claim_forbidden") else "可谈走势",
                    _sc.get("effective_days"), _sc.get("days_with_stance"),
                    ("—— " + "；".join(_sc.get("reasons") or [])) if _sc.get("reasons") else ""))
    _cp = ((d.get("change_points") or {}).get("per_stance") or {})
    if _cp:
        L.append("变点筛查：%s" % "；".join(
            "%s %d 个（强信号 %d）" % (st, len(v.get("change_points") or []),
                                      sum(1 for x in (v.get("change_points") or []) if x.get("strong")))
            for st, v in _cp.items()))
    _cand = d.get("candidates")
    if _cand:
        if _cand.get("available", True):
            L.append("候选立场（%s）：%s 簇，其中疑似未命名 %s 个"
                     % ((_cand.get("method") or "?")[:24], _cand.get("cluster_count"),
                        _cand.get("suspected_unnamed_count")))
        else:
            L.append("候选立场：未生效（%s）" % (_cand.get("reason") or "")[:60])
    _ll = d.get("llm_checks") or {}
    if _ll:
        L.append("LLM 校验：词典层×LLM 层一致率 %s%%%s"
                 % ((_ll.get("confusion") or {}).get("agreement_pct"),
                    "；双模型 κ %s" % (((_ll.get("model_agreement") or {}).get("kappa")))
                    if (_ll.get("model_agreement") or {}).get("kappa") is not None else ""))
    _dr = d.get("drift_report")
    if _dr:
        _items = _dr.get("items") or []
        L.append("口径漂移：%s" % ("无" if not _items else
                                  "；".join("%s %s→%s" % (x.get("item"), x.get("before"), x.get("after"))
                                             for x in _items[:4])))
    _ir = d.get("irony_note") or {}
    if _ir.get("suspect_records"):
        L.append("反讽标记：%s 条疑似（只做复核提示，未改判）" % _ir.get("suspect_records"))
    if sc:
        for k, v in sc.items():
            if isinstance(v, dict):
                extra = ""
                if v.get("overall_error_rate") is not None:
                    extra = "总体误判率 %s%%%s" % (v.get("overall_error_rate"),
                                                  " ｜ κ %s" % ((v.get("intercoder") or {}).get("cohen_kappa"))
                                                  if (v.get("intercoder") or {}).get("cohen_kappa") is not None else "")
                elif v.get("p_with_stance") is not None:
                    extra = "含立场表达 %s%%（分层 %s%%）" % (
                        v.get("p_with_stance"),
                        (v.get("stratified") or {}).get("p_with_stance"))
                L.append("抽检 %s：%s%s" % (k, v.get("status", "?"), (" ｜ " + extra) if extra else ""))
    else:
        L.append("抽检：未回写（尚未跑 spotcheck --apply）")
    alerts = []
    if (d.get("coverage_rate") is not None and thr.get("coverage_target") is not None
            and float(d["coverage_rate"]) < float(thr["coverage_target"])):
        alerts.append("覆盖率 %s%% < 目标 %s%%（不得写「主流/压倒性」）"
                      % (d["coverage_rate"], thr["coverage_target"]))
    if (inst.get("pct_of_total") or 0) > 15:
        alerts.append("机构帖占比超 15%")
    if d.get("rule_layer") and (d["rule_layer"] or {}).get("status") == "未抽检":
        alerts.append("规则层未抽检，只能读作占比上界")
    if d.get("llm_layer") and (d["llm_layer"] or {}).get("status") == "未抽检":
        alerts.append("LLM 层未抽检，只能读作占比上界")
    if sc and not all(isinstance(v, dict) and v.get("status") == "已完成" for v in sc.values()):
        alerts.append("存在未完成的抽检核")
    # v0.7：读取端的告警必须与写端一致（digest 只读文件，故一律从 d 取）
    _scd = d.get("selfcheck") or {}
    if _scd.get("trend_claim_forbidden"):
        alerts.append("不得谈走势（有效日 %s）：%s"
                      % (_scd.get("effective_days"), "；".join(_scd.get("reasons") or [])))
    _drd = d.get("drift_report") or {}
    if _drd.get("items"):
        alerts.append("口径漂移 %d 项（用 --prev 报告核对）" % len(_drd["items"]))
    _cnd = d.get("candidates") or {}
    if _cnd.get("suspected_unnamed_count"):
        alerts.append("发现 %d 个疑似未命名立场的候选簇（仅建议，未入表）"
                      % _cnd["suspected_unnamed_count"])
    if _cnd and _cnd.get("available") is False:
        alerts.append("嵌入增强未生效（已降级为披露）")
    L.append("告警：%s" % ("；".join(alerts) if alerts else "无"))
    L.append("读取建议：本文件 %.1f KB，已含明细；要明细读同目录 opinion.md，"
             "如需某字段请按字段查（勿整体读取本 JSON）" % kb)
    print("\n".join(L))
    return 0


def _irony_signals(text, hits):
    """反讽/否定的多线索标记（E3.5）：只在命中立场词的前提下标记。

    为什么只加标记不改判：反讽是"说话人与字面相反"，正则无法判定；一旦自动改判，
    标签与误判率会同时被污染。这里的定位是**提高人工复核的命中率**——
    被标记的记录会优先进抽检的富集块（spotcheck 的确定度评分直接吃这个字段）。
    """
    if not hits:
        return []
    out = []
    for name, pat in _IRONY_PATTERNS:
        try:
            if re.search(pat, text or ""):
                out.append(name)
        except re.error:
            continue
    return out


def _intensity_level(text, cfg):
    """表态强度档（E3.4）：按强度词表命中判定，取命中最多的档；无命中返回 None。

    强度**不进分母、不改占比**，只是给报告措辞分级的描述维度（避免把情绪强度当立场强度）。
    """
    best, best_n = None, 0
    for level, kws in (cfg or {}).items():
        n = sum(1 for k in kws if k and k in (text or ""))
        if n > best_n:
            best, best_n = level, n
    return best


def load_intensity(path=""):
    """从 stances.json 的可选 `intensity` 键读强度词表：{"强烈": [...], "温和": [...]}。"""
    if not (path and os.path.exists(path)):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except Exception:
        return {}
    if not isinstance(cfg, dict):
        return {}
    it = cfg.get("intensity")
    if not isinstance(it, dict):
        return {}
    return {str(k): [str(x) for x in v if isinstance(x, str) and x.strip()]
            for k, v in it.items() if isinstance(v, list) and v}


def load_events(path=""):
    """读事件清单（E4.3）：[{"time","label","source"}]；**无 source 的条目一律不输出**。"""
    if not (path and os.path.exists(path)):
        return []
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            obj = json.load(f)
    except Exception as e:
        print("[opinion] 警告：--events 读取失败（%s），本次不做事件对齐" % e)
        return []
    rows = obj.get("events") if isinstance(obj, dict) else obj
    if not isinstance(rows, list):
        return []
    out = []
    for x in rows:
        if not isinstance(x, dict):
            continue
        t = str(x.get("time") or "")[:10]
        if not t or not x.get("label"):
            continue
        if not x.get("source"):
            print("[opinion] 提示：事件「%s」缺 source，已跳过（无来源的事件时点不得进入报告）" % x.get("label"))
            continue
        out.append({"time": t, "label": str(x["label"]), "source": str(x["source"])})
    return sorted(out, key=lambda e: e["time"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--digest", default="",
                    help="只打印一屏摘要（≤25 行）后退出：**读工件的推荐方式**，避免整体读取大 JSON")
    ap.add_argument("--event", default="")
    ap.add_argument("--stances", default="", help="事件定制立场词典 json（{\"立场\": [\"关键词\",...]}）；"
                                                  "关键词支持 re: 前缀正则")
    ap.add_argument("--institutional", default="", help="机构识别规则 json（可选，与内置规则合并）")
    ap.add_argument("--rules", default="", help="规则层口语判据 json（可选，**必须显式传入才启用**）："
                                                "{\"立场\": {\"positive\": [词], \"patterns\": [正则]}, "
                                                "\"noise_patterns\": [正则]}；与词典层并列输出，不合并")
    ap.add_argument("--llm-labels", dest="llm_labels", default="",
                    help="外部 LLM 标注导入（可选，层化架构的 llm 层）：JSONL/JSON 数组/{\"records\":[...]}，"
                         "每项 {platform,id,stance[,confidence]}；与词典层并列输出、不合并、不改分母。"
                         "本脚本不联网、不调用模型")
    ap.add_argument("--total-raw", type=int, default=None,
                    help="语料治理前的原始条数（可选）。不传即「未知」，输出 null；不要用 0 表示未知")
    ap.add_argument("--mid-samples", type=int, default=None,
                    help="残差中位段抽样条数（缺省取 config.json 的 mid_samples，再缺省 12）")
    ap.add_argument("--minority-threshold", type=float, default=None,
                    help="少数派判定阈值（百分点；缺省取 config.json 的 minority_threshold，再缺省 10）")
    ap.add_argument("--coverage-target", type=float, default=None,
                    help="归类覆盖率目标（百分点；缺省取 config.json 的 coverage_target，再缺省 70）。"
                         "低于目标只告警，不改任何数字")
    ap.add_argument("--terms", choices=["off", "jieba"], default="off",
                    help="可选增强：jieba=额外输出词级高频词诊断（需已安装 jieba，否则降级并披露）。"
                         "增强项不参与立场判定")
    ap.add_argument("--structure", choices=["on", "off"], default="on",
                    help="声量与人数结构分析（默认 on）：独立作者数、条/人比、发文集中度 HHI、"
                         "复制粘贴/近重复簇。只做披露，不剔除记录、不改分母")
    ap.add_argument("--dup-threshold", type=int, default=3,
                    help="近重复判定：64 位 SimHash 的汉明距离阈值（默认 3，越小越严）")
    ap.add_argument("--heavy-threshold", type=int, default=5,
                    help="高频账号阈值：单账号记录数 ≥ 该值计入「高频账号」（默认 5）")
    # ---- v0.7 新增 ----
    ap.add_argument("--llm-labels-b", dest="llm_labels_b", default="",
                    help="第二个 LLM 标注文件（可选，E3.1）：与 --llm-labels 交叉比对，"
                         "输出两模型一致性（含 κ）与词典层混淆矩阵")
    ap.add_argument("--events", default="",
                    help="事件清单 json（可选，E4.3）：[{\"time\",\"label\",\"source\"}]，"
                         "无 source 的条目会被跳过。脚本不联网、不解析新闻，清单由 ②揽风云 或人工提供")
    ap.add_argument("--event-window", type=int, default=3,
                    help="事件对齐窗口（天，默认 3）：取事件日前后各 N 天的 A 层记录做前后对比")
    ap.add_argument("--change-points", choices=["on", "off"], default="on",
                    help="变点筛查（E4.2，默认 on）：对每个立场做 CUSUM，输出候选变点（含强弱判定）")
    ap.add_argument("--prev", default="",
                    help="上次的 opinion.json（可选，E5.3）：输出口径漂移报告（语料/词表/参数/脚本哪一项变了）")
    ap.add_argument("--candidates", choices=["off", "lexical", "embed"], default="lexical",
                    help="候选立场发现（E3.2）：lexical=字面法（默认，零依赖）；"
                         "embed=嵌入法（需 sentence-transformers，未装则降级并披露）；off=关闭")
    ap.add_argument("--candidate-min-size", type=int, default=4,
                    help="候选簇最小规模（默认 4）")
    ap.add_argument("--trend-min-days", type=int, default=None,
                    help="谈走势所需最少有效日数（缺省取 config.json 的 trend_min_days，再缺省 3）")
    ap.add_argument("--trend-min-day-denominator", type=int, default=None,
                    help="单日有效分母下限（缺省取 config.json 的 trend_min_day_denominator，再缺省 20）")
    args = ap.parse_args()

    # --digest：只读工件的摘要模式，不跑分析
    if args.digest:
        return digest(args.digest)
    if not args.src or not args.out:
        ap.error("--in 与 --out 为必需参数（或用 --digest <opinion.json> 只看摘要）")

    cfg = load_config()
    mid_samples, mid_src = pick(args.mid_samples, cfg, "mid_samples")
    minority_threshold, minority_src = pick(args.minority_threshold, cfg, "minority_threshold")
    coverage_target, coverage_src = pick(args.coverage_target, cfg, "coverage_target")
    trend_min_days, tmd_src = pick(args.trend_min_days, cfg, "trend_min_days")
    trend_min_day_n, tmn_src = pick(args.trend_min_day_denominator, cfg, "trend_min_day_denominator")
    threshold_src = {"mid_samples": mid_src, "minority_threshold": minority_src,
                     "coverage_target": coverage_src,
                     "trend_min_days": tmd_src, "trend_min_day_denominator": tmn_src}

    stances = load_stances(args.stances)
    if not stances:
        print("[opinion] 错误：未提供立场词典（--stances 缺失或 config.json stance_keywords 为空）。")
        print("[opinion] 立场词典须按事件定制：依据采集内容与首轮搜索的观点分歧构造 {立场: [关键词,...]}，")
        print('[opinion] 例如：opinion.py --in <normalized.jsonl> --out <opinion.json> --event "事件" --stances data/<event_id>/stances.json')
        return 1
    inst_cfg = load_institutional(args.institutional)
    # D4：v0.5 起只有显式 --rules 才启用规则层。旧实现会自动加载 --out 同目录的
    # opinion_rules.json，于是「无规则层」的对照实验其实带着规则层，排障时系统性误判。
    rules, noise_patterns = load_rules(args.rules)
    if not args.rules:
        cand = os.path.join(os.path.dirname(os.path.abspath(args.out)), "opinion_rules.json")
        if os.path.exists(cand):
            print("[opinion] 提示：检测到同目录 %s。v0.5 起不再隐式加载规则层，"
                  "如需启用请显式传 --rules %s" % (cand, cand))
    llm_labels, llm_rows, llm_conf = load_llm_labels(args.llm_labels)
    llm_labels_b, llm_rows_b, _llm_conf_b = load_llm_labels(args.llm_labels_b)
    intensity_cfg = load_intensity(args.stances)
    events = load_events(args.events)
    prev_opinion = None
    if args.prev:
        try:
            with open(args.prev, "r", encoding="utf-8-sig") as f:
                prev_opinion = json.load(f)
        except Exception as e:
            print("[opinion] 警告：--prev 读取失败（%s），本次不做漂移比对" % e)

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
    day_authors = defaultdict(set)         # E4.1：每日独立作者数（每日分母的另一半）
    A, B, C = [], [], []          # 个人表达 / 机构帖 / 未识别残差
    inst_reason = Counter()
    labels = {}
    neg_total = 0
    irony_total = 0
    intensity_of = {}                      # E3.4：记录 → 表态强度档
    R = []                        # 规则层命中（与 A 层并列，不合并）
    rule_dist = defaultdict(list)
    rule_df = Counter()
    L = []                        # LLM 标注层命中（导入式，同样并列不合并）
    llm_dist = defaultdict(list)

    for r in recs:
        text = r.get("content", "") or ""
        label, matched, negated = classify(text, stances)
        neg_total += len(negated)
        # E3.5：反讽/否定的**多线索标记**（只标记，绝不自动改判）
        irony = _irony_signals(text, matched)
        irony_total += 1 if irony else 0
        if intensity_cfg and label != OTHER:
            intensity_of[id(r)] = _intensity_level(text, intensity_cfg)
        for kws in stances.values():
            for kw in kws:
                if _hit(kw, text):
                    kw_df[kw] += 1
        if text and r.get("time"):
            timeline[str(r["time"])[:10]] += 1
        labels[id(r)] = (label, matched, negated, irony)
        inst, why = is_institutional(r, inst_cfg)
        if inst:
            inst_reason[why] += 1
            B.append(r)
        elif label == OTHER:
            C.append(r)
        else:
            A.append(r)
            if r.get("time"):
                day_authors[str(r["time"])[:10]].add(str(r.get("author") or "(未署名)"))
        # 焦点转移（D2）：**无条件**统计，且只收 A 层立场。
        #   旧实现把这一行写在 `if rules and not inst:` 块内 → 不给 --rules 就恒为 {}，
        #   报告的堆叠条与趋势折线整体消失；而且它把「未识别」也写成键，
        #   于是某天只有未识别时会被渲染成空白行（有记录的一天画成没有）。
        if r.get("time") and not inst and label != OTHER:
            day_stance[str(r["time"])[:10]][label] += 1
        # 规则层：对全部非机构个人记录独立跑一遍（口径与词典层一致：非机构帖）
        if rules and not inst:
            rl, rh = classify_rules(text, rules)
            if rl:
                R.append(r)
                rule_dist[rl].append(r)
                for h in rh:
                    rule_df[h] += 1

    # LLM 标注层（导入式）：与词典层并列，不合并、不改分母
    if llm_labels:
        for r in recs:
            st = llm_labels.get((r.get("platform"), str(r.get("id") or "")))
            if st:
                L.append(r)
                llm_dist[st].append(r)
    llm_dist_b = defaultdict(list)
    if llm_labels_b:
        for r in recs:
            st = llm_labels_b.get((r.get("platform"), str(r.get("id") or "")))
            if st:
                llm_dist_b[st].append(r)

    total = len(recs)

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
            lab, matched, negated, irony = labels[id(r)]
            smp.append({"author": r.get("author", ""), "likes": (r.get("metrics") or {}).get("likes", 0),
                        "content": (r.get("content") or "")[:300], "url": _canon_url(r.get("url")),
                        "platform": r.get("platform", ""), "id": str(r.get("id") or ""),
                        "ref_url": ref_url, "ref_how": how, "matched": matched, "negated": negated,
                        # E3.5：多线索的反讽/否定标记（只提示复核，不改判）
                        "irony_suspect": irony})
        dist.append({
            "stance": s, "count": cnt, "pct": round(cnt / denom * 100, 1) if denom else 0,
            # v0.5 新增：占比的 95% 置信区间（Wilson）。让「40%」变成「40%（95%CI 32–48）」，
            # 是挡「覆盖率两成就敢写压倒性」的第一道闸——区间宽度随分母自动说话。
            "pct_ci95": list(stats.wilson_pct(cnt, denom)) if denom else [0.0, 0.0],
            # v0.6 新增：以**独立作者**为单位的人数（条数会被刷屏账号放大，人数不会）
            "author_count": len(set(str(r.get("author") or "(未署名)") for r in rows)),
            "likes": lk, "like_pct": 0.0,
            "minority": denom > 0 and cnt / denom * 100 < minority_threshold,
            # E3.4：表态强度分布（仅在 stances.json 提供 intensity 词表时出现；不进分母）
            "intensity_mix": (dict(Counter(intensity_of[id(r)] for r in rows
                                           if intensity_of.get(id(r))).most_common())
                              if intensity_cfg else None),
            "top_samples": smp,
        })
    like_sum = sum(d["likes"] for d in dist) or 1
    for d in dist:
        d["like_pct"] = round(d["likes"] / like_sum * 100, 1)

    # ---- 残差诊断（只取个人 UGC）+ 三段拆分（v0.4）----
    unc = [r for r in C if not is_institutional(r, inst_cfg)[0]]
    seg, waiting = split_residual(unc, noise_patterns)
    # 可判集合 vs 全部语料：后者把几百条本就不含立场表达的反应/玩梗/轶事也算成「我们没判出来」，
    # 于是大比例「未识别」被误读成「方法只能覆盖两成」（v0.4 起两个分母并排披露）。
    rule_only = [r for r in R if labels[id(r)][0] == OTHER]
    # 可判集合：CONTRACT-joint §9.1 定义为「A + 规则层独有 + 未分类段」——
    # 本字段**照契约实现，口径不动**（契约阶段一冻结）。
    judgeable = len(A) + len(rule_only) + len(waiting)
    # v0.5 新增（D9）：去掉规则层依赖的同一指标。契约口径里含「规则层独有」，
    # 于是同一份语料「跑/不跑规则层」会给出两个覆盖率（实测 50.0% / 33.3%），
    # 而 G7/G10 都用覆盖率判强度断言 → 覆盖率跨报告不可比。norule 版本只依赖词典层与语料，
    # 供跨报告比较；契约口径是否统一留阶段二（需改 CONTRACT，阶段一冻结）。
    judgeable_norule = len(A) + len(waiting)
    _wait_ids = set(id(r) for r in waiting)
    _overlap = sum(1 for r in rule_only if id(r) in _wait_ids)
    judgeable_breakdown = {
        "layer_A": len(A), "rule_only": len(rule_only), "unclassified_segment": len(waiting),
        "rule_only_overlap_with_segment": _overlap,
        "contract_sum": judgeable, "dedup_sum": judgeable - _overlap,
        "note": "契约口径 = A + 规则层独有 + 未分类段（§9.1，本脚本未改）。"
                "注意 rule_only 与未分类段存在重叠（重叠条数见 rule_only_overlap_with_segment），"
                "相加减会把重叠部分重复计入分母；coverage_of_judgeable_norule 去掉了规则层依赖，"
                "同一语料在「跑/不跑规则层」下同值，跨报告比较请用它。口径统一留阶段二。",
    }
    vocab = [k[3:] if k.startswith("re:") else k for kws in stances.values() for k in kws]
    blob = " ".join((r.get("content") or "") for r in unc)
    grams = Counter(g for g in re.findall(r"[\u4e00-\u9fff]{2,4}", blob) if g not in _STOP)
    top = [{"gram": g, "df": c} for g, c in grams.most_common(120)]
    diff = [x for x in top if not any(x["gram"] in k or k in x["gram"] for k in vocab)][:40]
    band = sorted(unc, key=lambda r: ((r.get("metrics") or {}).get("likes", 0) or 0))
    lo = int(len(band) * 0.45)
    mid = []
    for r in band[lo:lo + max(0, int(mid_samples or 0))]:
        ru, _ = _resolve_ref_url(r, id2url)
        mid.append({"author": r.get("author", ""), "likes": (r.get("metrics") or {}).get("likes", 0) or 0,
                    "content": (r.get("content") or "")[:160], "url": _canon_url(r.get("url")),
                    "ref_url": ru})

    # ---- 可选增强（enhance.py）：只在显式开启时执行，且**不参与立场判定** ----
    requested_enh = [args.terms] if args.terms and args.terms != "off" else []
    terms_diag = {"available": False, "terms": [], "reason": "未请求（--terms off）"}
    if args.terms == "jieba":
        terms_diag = enhance.jieba_top_terms(
            [(r.get("content") or "") for r in unc], topn=60,
            work_dir=os.path.dirname(os.path.abspath(args.out)), stop=_STOP)
    used_enh = [args.terms] if (args.terms == "jieba" and terms_diag.get("available")) else []
    pipeline = enhance.manifest(requested=requested_enh, used=used_enh)
    pipeline["layers"] = ["dict"] + (["rule"] if rules else []) + (["llm"] if llm_labels else [])
    pipeline["mode"] = "enhanced" if used_enh else "stdlib"
    pipeline["note"] = (pipeline["note"] + " 本次启用的层：%s。" % "、".join(pipeline["layers"])
                        + " 一句话纪律：同一语料在装了/没装可选库的机器上，默认路径的数字必须逐位相同；"
                          "只有显式开层才会变，且必须写在本字段里。")
    # 可选增强（--terms jieba）的产物只放在 pipeline 里，**不塞进任何既有字段**——
    # 这样「增强不改默认路径数字」这条纪律可以被机械核对（逐字段比对即可）。
    pipeline["terms_diagnostic"] = terms_diag

    # ---- 声量与人数（v0.6）：条数与人数并排、集中度、复制粘贴/近重复 ----
    def _author(r):
        return str(r.get("author") or "(未署名)")

    def _key(r):
        return (r.get("platform"), str(r.get("id") or ""))

    def _lab(r):
        return labels[id(r)][0]

    author_distribution = structure.author_stance_distribution(A, _lab) if A else None
    audience_structure = None
    dup_records_by_stance = {}
    if args.structure == "on":
        personal = A + C
        dup_map = structure.near_dup_map(personal, _key, lambda r: r.get("content") or "",
                                        threshold=args.dup_threshold)
        dup_report = structure.duplication_report(dup_map, personal, _key, threshold=args.dup_threshold)
        # 每个立场里有多少条落在近重复簇内（只披露，不进分母、不剔除）
        _dup_keys = set(dup_map.get("flags") or {})
        for d in dist:
            st = d["stance"]
            dup_records_by_stance[st] = sum(1 for r in by_stance.get(st, []) if _key(r) in _dup_keys)
        # 以作者为簇的比例修正：同一作者多条记录不独立，直接算会低估标准误
        a_clusters = defaultdict(lambda: {"n": 0, "by_stance": Counter()})
        for r in A:
            c = a_clusters[_author(r)]
            c["n"] += 1
            c["by_stance"][_lab(r)] += 1
        clustered = {}
        for d in dist:
            cr = stats.clustered_proportion(
                [c["by_stance"].get(d["stance"], 0) for c in a_clusters.values()],
                [c["n"] for c in a_clusters.values()])
            if cr:
                clustered[d["stance"]] = {
                    "p_pct": cr["p_pct"], "ci95": list(cr["ci95_pct"]),
                    "design_effect": cr["design_effect"], "effective_n": cr["effective_n"],
                    "n_clusters": cr["n_clusters"], "cluster_sizes": cr["cluster_sizes"],
                }
        _auth_total = (author_distribution or {}).get("authors_with_stance") or 0
        audience_structure = {
            "layers": {
                "A_个人表达": structure.author_report(A, _key, _lab, n_top=8,
                                                      heavy_threshold=args.heavy_threshold),
                "B_机构帖": structure.author_report(B, _key, _lab, n_top=5,
                                                     heavy_threshold=args.heavy_threshold),
                "C_未识别残差": structure.author_report(C, _key, _lab, n_top=5,
                                                        heavy_threshold=args.heavy_threshold),
            },
            "per_stance_by_author": [
                {"stance": d["stance"], "records": d["count"], "authors": d["author_count"],
                 "posts_per_author": (round(d["count"] / d["author_count"], 2)
                                      if d["author_count"] else None),
                 "pct_by_author": (round(d["author_count"] / _auth_total * 100, 1)
                                   if _auth_total else None),
                 "records_in_dup_clusters": dup_records_by_stance.get(d["stance"], 0)}
                for d in dist],
            "clustered_by_author": clustered,
            "duplication": dup_report,
            "dup_records_by_stance": dup_records_by_stance,
            "note": "本区块只做结构披露：**不剔除任何记录、不改任何分母**。"
                    "条数占比回答「发了多少」，人数占比回答「多少人」；两者差距越大，"
                    "越说明声量集中在少数账号。pct_by_author 的分母 = 至少有一条立场表达的作者数"
                    "（作者可跨立场，故之和可能 >100%）。同源文本不等于水军——转述同一新闻也会同源，"
                    "要下判断必须先人工抽样核并写进报告。",
        }

    # ---- 两立场之差：同一批样本内部比较，必须用多项口径（不能用独立双样本公式）----
    stance_comparisons = []
    for _i in range(len(dist)):
        for _j in range(_i + 1, len(dist)):
            _ci = stats.multinomial_diff_ci(dist[_i]["count"], dist[_j]["count"], denom)
            stance_comparisons.append({
                "pair": [dist[_i]["stance"], dist[_j]["stance"]],
                "diff_pp": round((dist[_i]["count"] - dist[_j]["count"]) / denom * 100, 1) if denom else 0.0,
                "ci95_pp": [round(_ci[0] * 100, 1), round(_ci[1] * 100, 1)],
                "verdict": stats.diff_verdict(_ci),
            })
    _ci50 = stats.wilson_interval(denom // 2, denom) if denom else (0.0, 1.0)
    precision = {
        "denominator": denom,
        "half_width_pp_at_p50": round((_ci50[1] - _ci50[0]) / 2 * 100, 1) if denom else None,
        "required_n_for_margin": {"±5pp": stats.required_n(0.5, 0.05, N=total),
                                  "±10pp": stats.required_n(0.5, 0.1, N=total)},
        "note": "half_width_pp_at_p50 = 当前分母下占比 50% 处的 95% 区间半宽，"
                "它就是「这批数字能撑起多细的话」的上限（分母只有几十条时，±十几个百分点是常态）；"
                "required_n_for_margin 给出要达到 ±5pp / ±10pp 需要多大的立场分母（含有限总体校正）。",
    }

    # ================= v0.7 新增块 =================

    # ---- E4.1 每日明细：分母 / 占比 / 区间 / 独立作者 ----
    stance_timeline_detail = {}
    for _day in sorted(day_stance):
        _seg = day_stance[_day]
        _n = sum(_seg.values())
        if _n <= 0:
            continue
        stance_timeline_detail[_day] = {
            "day": _day,
            "records_total": timeline.get(_day, 0),
            "denominator": _n,
            "authors": len(day_authors.get(_day) or set()),
            "by_stance": dict(_seg),
            "pct": {s: round(c / _n * 100, 1) for s, c in _seg.items()},
            "ci95": {s: list(stats.wilson_pct(c, _n)) for s, c in _seg.items()},
        }

    # ---- E4.2 变点筛查（CUSUM）：只对占比最高的前几个立场做，避免噪声堆积 ----
    change_points = None
    if args.change_points == "on" and stance_timeline_detail:
        _cp = {}
        _analyzed = [d["stance"] for d in dist[:3]]
        for st in _analyzed:
            series = [{"day": day, "k": (stance_timeline_detail[day]["by_stance"].get(st) or 0),
                       "n": stance_timeline_detail[day]["denominator"]}
                      for day in sorted(stance_timeline_detail)]
            r = stats.change_points(series)
            if r.get("change_points"):
                _cp[st] = r
        change_points = {"per_stance": _cp, "analyzed_stances": _analyzed,
                         "h": 4.0,
                         "note": "变点是**候选提示**：strong=false 的条目属弱信号，不得据此写"
                                 "「走势转折」；本块不构成因果判断。"}

    # ---- E4.3 事件对齐：只做时间对齐描述，不声称因果 ----
    event_alignment = None
    if events:
        _by_day = defaultdict(list)
        for r in A:
            t = str(r.get("time") or "")[:10]
            if t:
                _by_day[t].append(r)

        def _mix(rows):
            c = Counter(labels[id(r)][0] for r in rows)
            n = sum(c.values())
            return c, n, ({s: round(v / n * 100, 1) for s, v in c.items()} if n else {})

        _al = []
        for ev in events:
            try:
                center = datetime.date.fromisoformat(ev["time"])
            except ValueError:
                continue
            pre, post = [], []
            for d, rs in _by_day.items():
                try:
                    dd = datetime.date.fromisoformat(d)
                except ValueError:
                    continue
                off = (dd - center).days
                if -int(args.event_window) <= off < 0:
                    pre += rs
                elif 0 <= off <= int(args.event_window):
                    post += rs
            c1, n1, p1 = _mix(pre)
            c2, n2, p2 = _mix(post)
            entry = {"event": ev, "window_days": int(args.event_window),
                     "before": {"records": n1, "pct": p1}, "after": {"records": n2, "pct": p2},
                     "shifts": {},
                     "caveat": "仅时间相邻，不构成因果（脚本不做因果推断）"}
            for st in [d["stance"] for d in dist[:2]]:
                k1, k2 = c1.get(st, 0), c2.get(st, 0)
                if n1 > 0 and n2 > 0:
                    ci = stats.newcombe_diff_ci(k2, n2, k1, n1)
                    entry["shifts"][st] = {
                        "before_pct": round(k1 / n1 * 100, 1),
                        "after_pct": round(k2 / n2 * 100, 1),
                        "diff_pp": round((k2 / n2 - k1 / n1) * 100, 1),
                        "diff_ci95_pp": [round(ci[0] * 100, 1), round(ci[1] * 100, 1)],
                        "verdict": stats.diff_verdict(ci),
                    }
                else:
                    # 窗口内一侧无记录时**不给差异判断**（n=0 的区间没有意义，
                    # 若照算会输出 [-100, 100] 这种看似结论的噪声）
                    entry["shifts"][st] = {
                        "before_pct": (round(k1 / n1 * 100, 1) if n1 else None),
                        "after_pct": (round(k2 / n2 * 100, 1) if n2 else None),
                        "diff_pp": None, "diff_ci95_pp": None,
                        "insufficient": True,
                        "verdict": "窗口内至少一侧无 A 层记录，不做差异判断",
                    }
            if not n1 or not n2:
                entry["window_note"] = "该事件窗口内前 %d 条 / 后 %d 条：样本不足，仅登记时点" % (n1, n2)
            _al.append(entry)
        event_alignment = {"events": _al, "n_events": len(_al),
                           "note": "事件时点由 --events 提供（每条必须带 source）；"
                                   "本块只做时间对齐与前后差异描述，**不得读作因果**。"}

    # ---- E4.5 单一时点不得谈走势的机器判据 ----
    _eff_days = [d for d, v in stance_timeline_detail.items()
                 if v["denominator"] >= int(trend_min_day_n)]
    _forbid = len(_eff_days) < int(trend_min_days)
    _reasons = []
    if len(stance_timeline_detail) < int(trend_min_days):
        _reasons.append("时间跨度不足：仅 %d 天有立场表达（要求 ≥%d）"
                        % (len(stance_timeline_detail), int(trend_min_days)))
    if len(_eff_days) < int(trend_min_days):
        _reasons.append("有效日不足：分母 ≥%d 的只有 %d 天（要求 ≥%d）"
                        % (int(trend_min_day_n), len(_eff_days), int(trend_min_days)))
    if denom and denom < 30:
        _reasons.append("立场分母 %d < 30：占比区间过宽，任何趋势描述都缺乏统计支撑" % denom)
    selfcheck = {
        "days_with_stance": len(stance_timeline_detail),
        "effective_days": len(_eff_days),
        "effective_days_list": sorted(_eff_days),
        "trend_claim_forbidden": bool(_forbid),
        "reasons": _reasons,
        "thresholds": {"trend_min_days": int(trend_min_days),
                       "trend_min_day_denominator": int(trend_min_day_n),
                       "sources": {"trend_min_days": tmd_src, "trend_min_day_denominator": tmn_src}},
        "note": "trend_claim_forbidden=true 时，报告不得出现「走势 / 转向 / 走高 / 回落」等表述；"
                "该字段是给报告与（阶段二）门禁读的机器判据，不是建议。",
    }

    # ---- E3.2 候选立场发现（只出建议，不改判定）----
    candidates = None
    if args.candidates != "off":
        _vocab_terms = [k[3:] if k.startswith("re:") else k
                        for kws in stances.values() for k in kws]
        _pool = unc or C
        if args.candidates == "lexical":
            candidates = structure.candidate_clusters(
                _pool, _key, lambda r: r.get("content") or "", vocab=_vocab_terms,
                min_size=int(args.candidate_min_size), stop=_STOP)
        else:
            _emb = enhance.embed_clusters([(r.get("content") or "") for r in _pool],
                                          min_size=int(args.candidate_min_size))
            candidates = {"method": "sentence-transformers embeddings", **_emb}
            pipeline["requested"] = sorted(set((pipeline.get("requested") or [])
                                               + ["sentence_transformers"]))
            if _emb.get("available"):
                pipeline["used"] = sorted(set((pipeline.get("used") or []) + ["sentence_transformers"]))
                pipeline["mode"] = "enhanced"
            else:
                pipeline["degraded"] = (pipeline.get("degraded") or []) + [
                    {"name": "sentence_transformers", "reason": _emb.get("reason", "")}]

    # ---- E3.1 LLM 层校验：混淆矩阵 / 置信度分箱 / 双模型一致性 ----
    llm_checks = None
    if llm_labels:
        cells = Counter()
        cell_ids = defaultdict(list)
        for r in recs:
            k = (r.get("platform"), str(r.get("id") or ""))
            st = llm_labels.get(k)
            if not st:
                continue
            dl = labels[id(r)][0]
            cells[(dl, st)] += 1
            if len(cell_ids[(dl, st)]) < 1:
                cell_ids[(dl, st)].append(str(r.get("id") or ""))
        _agree = sum(v for (a, b), v in cells.items() if a == b)
        _tot = sum(cells.values())
        conf_bins = {}
        for _lo, _hi, _name in ((0.0, 0.6, "<0.6"), (0.6, 0.8, "0.6-0.8"), (0.8, 1.01, "≥0.8")):
            n_a = n_t = 0
            for r in recs:
                k = (r.get("platform"), str(r.get("id") or ""))
                st = llm_labels.get(k)
                c = llm_conf.get(k)
                if not st or c is None:
                    continue
                if _lo <= float(c) < _hi:
                    n_t += 1
                    n_a += 1 if st == labels[id(r)][0] else 0
            if n_t:
                conf_bins[_name] = {"n": n_t, "agree_with_dict": n_a,
                                    "agree_pct": round(n_a / n_t * 100, 1)}
        b_agree = None
        if llm_labels_b:
            pairs = [(llm_labels[k], llm_labels_b[k]) for k in set(llm_labels) & set(llm_labels_b)]
            if pairs:
                kd = stats.cohen_kappa(pairs)
                kd.pop("n", None)          # 与 n_pairs 重复，去掉以免两个"n"打架
                kd_flt = {kk: (round(vv, 3) if isinstance(vv, float) else vv) for kk, vv in kd.items()}
                b_agree = {"n_pairs": len(pairs), **kd_flt,
                           "reading": stats.reliability_note(kd.get("kappa"), None)}
        llm_checks = {
            "confusion": {
                "rows": "词典层", "cols": "LLM 层",
                "cells": [{"dict": a, "llm": b, "n": v, "sample_id": (cell_ids[(a, b)] or [""])[0]}
                          for (a, b), v in sorted(cells.items(), key=lambda kv: -kv[1])[:40]],
                "agreement_pct": round(_agree / _tot * 100, 1) if _tot else None,
                "n_labeled": _tot,
            },
            "confidence_bins": conf_bins or None,
            "model_agreement": b_agree,
            "note": "LLM 层与词典层**并列不合并**：对比只用来说明「哪一类记录机器与模型打架」，"
                    "双方的判断在人工抽检写出误判率之前都只是上界。",
        }

    # ---- E5.2 词表构造建议（零依赖；只出建议，不改词典）----
    def _jaccard_bigrams(a, b):
        ga = {a[i:i + 2] for i in range(max(0, len(a) - 1))}
        gb = {b[i:i + 2] for i in range(max(0, len(b) - 1))}
        if not ga or not gb:
            return 0.0
        return len(ga & gb) / float(len(ga | gb))

    _vocab_all = [k[3:] if k.startswith("re:") else k for kws in stances.values() for k in kws]
    _adv_terms = []
    for x in diff[:20]:
        best, bs = None, 0.0
        for v in _vocab_all:
            s = _jaccard_bigrams(x["gram"], v)
            if s > bs:
                best, bs = v, s
        _adv_terms.append({
            "term": x["gram"], "df": x["df"], "closest_keyword": best,
            "similarity": round(bs, 2),
            "hint": ("疑似变体（与「%s」高度重合）" % best) if bs >= 0.5
                    else "疑似新表达（与现有词表重合度低，可交 LLM 人工判读）",
        })
    vocabulary_advice = {
        "candidate_terms": _adv_terms,
        "note": "候选来自「残差里出现、但词表未覆盖」的高频字 n-gram；是否入表由人/LLM 决定，"
                "改词表会改口径，须同步 codebook 并用 vocab.py --diff 留痕。",
    }

    # ---- E5.1 run_manifest：输入/参数/词表/脚本指纹（不含时间戳，保证可复现）----
    run_manifest = manifest_mod.build(
        inputs=[(args.src, True, "corpus"), (args.stances, False, "stances"),
                (args.institutional, False, "institutional"), (args.rules, False, "rules"),
                (args.llm_labels, False, "llm_labels"), (args.llm_labels_b, False, "llm_labels_b"),
                (args.events, False, "events"), (args.prev, False, "prev")],
        args=args,
        vocabulary=stances,
        script_path=os.path.abspath(__file__),
        extra={"config_source": cfg.get("_source"), "threshold_sources": threshold_src},
    )

    # ---- E5.3 口径漂移告警 ----
    drift_report = None
    if prev_opinion is not None:
        _items = []

        def _num(d, *keys):
            cur = d
            for k in keys:
                cur = (cur or {}).get(k) if isinstance(cur, dict) else None
            return cur

        _cov_now = round(denom / total * 100, 1) if total else 0
        _pairs = [
            ("立场分母", _num(prev_opinion, "denominator"), denom, "count"),
            ("归类覆盖率", _num(prev_opinion, "coverage_rate"), _cov_now, "pp"),
            ("语料条数", _num(prev_opinion, "total_records"), total, "count"),
            ("机构帖", _num(prev_opinion, "institutional_layer", "count"), len(B), "count"),
            ("未识别残差", _num(prev_opinion, "residual", "count"), len(C), "count"),
        ]
        for name, old, new, kind in _pairs:
            if old is None or new is None:
                continue
            if kind == "pp":
                delta = float(new) - float(old)
                if abs(delta) >= float(cfg.get("drift_coverage_pp", 5)):
                    _items.append({"item": name, "before": old, "after": new,
                                   "delta": round(delta, 1),
                                   "why": "覆盖率变化超过阈值 %.1f 个百分点"
                                          % float(cfg.get("drift_coverage_pp", 5))})
            else:
                if old == 0 and new == 0:
                    continue
                base = max(1, int(old))
                pct = (int(new) - int(old)) / float(base) * 100
                if abs(pct) >= float(cfg.get("drift_denominator_pct", 10)):
                    _items.append({"item": name, "before": old, "after": new,
                                   "delta_pct": round(pct, 1),
                                   "why": "变化超过阈值 %s%%" % cfg.get("drift_denominator_pct", 10)})
        _mdc = manifest_mod.compare(_num(prev_opinion, "run_manifest"), run_manifest)
        for x in _mdc:
            if x["item"] in ("输入内容变化", "输入消失", "输入新增", "词表变化", "参数变化"):
                _items.append({"item": x["item"], "detail": x["detail"],
                               "why": "指纹比对发现"})
        drift_report = {
            "prev": args.prev,
            "items": _items,
            "manifest_diff": _mdc,
            "thresholds": {"drift_coverage_pp": cfg.get("drift_coverage_pp", 5),
                           "drift_denominator_pct": cfg.get("drift_denominator_pct", 10)},
            "note": "漂移不等于错误：它回答「这次数字变了，是语料变了、词表变了，还是方法变了」。"
                    "没有任何一项时说明口径与输入都没变，数字差异只可能来自随机（本脚本无随机成分）。",
        }

    # ---- 一屏摘要（v0.7）----
    # 目的：工件会随语料增长（真实事件可达上百 KB），整体读取会吃掉大量上下文。
    # summary 是**扁平化的结论层**（≤2 KB）：agent 只读它就能写报告/下判断；
    # 明细留在本文件其余字段与 opinion.md 里，按需查。
    _alerts = []
    if total and (denom / total * 100) < float(coverage_target):
        _alerts.append("覆盖率低于目标 %s%%" % coverage_target)
    if total and len(B) / total > 0.15:
        _alerts.append("机构帖占比 >15%，判定规则需人工确认")
    if rules:
        _alerts.append("规则层未抽检（只能读作占比上界）")
    if llm_labels:
        _alerts.append("LLM 层未抽检（只能读作占比上界）")
    summary = {
        "event": args.event,
        "total_records": total,
        "layers": {"A": denom, "B": len(B), "C": len(C),
                   "D_noise": (max(0, int(args.total_raw) - total) if args.total_raw is not None else None)},
        "coverage_rate": round(denom / total * 100, 1) if total else 0,
        "coverage_of_judgeable": round(denom / judgeable * 100, 1) if judgeable else 0,
        "coverage_of_judgeable_norule": (round(denom / judgeable_norule * 100, 1)
                                         if judgeable_norule else 0),
        "coverage_target": coverage_target,
        "stances": [{"stance": d["stance"], "count": d["count"], "pct": d["pct"],
                     "ci95": d["pct_ci95"], "authors": d["author_count"],
                     "minority": d["minority"]} for d in dist],
        "top_difference": (stance_comparisons[0] if stance_comparisons else None),
        "precision_half_width_pp": precision.get("half_width_pp_at_p50"),
        "institutional_pct": round(len(B) / total * 100, 1) if total else 0,
        "audience": ({
            "records": (audience_structure["layers"]["A_个人表达"] or {}).get("records"),
            "authors": (audience_structure["layers"]["A_个人表达"] or {}).get("unique_authors"),
            "hhi_x10000": (((audience_structure["layers"]["A_个人表达"] or {}).get("concentration") or {})
                           .get("hhi_x10000")),
            "top1_share_pct": (((audience_structure["layers"]["A_个人表达"] or {}).get("concentration") or {})
                               .get("top1_share_pct")),
            "cross_account_dup": (audience_structure.get("duplication") or {}).get("cross_account_dup_records"),
            "near_dup_records": (audience_structure.get("duplication") or {}).get("near_duplicate_records"),
        } if audience_structure else None),
        "layers_extra": {"rule": bool(rules), "llm": bool(llm_labels),
                         "rule_count": len(R), "llm_count": len(L)},
        "pipeline_mode": pipeline.get("mode"),
        # v0.7：把新增的闸门与自查结果也放进摘要，agent 读摘要即可判"能不能写走势/有没有漂移"
        "trend_claim_forbidden": selfcheck.get("trend_claim_forbidden"),
        "effective_days": selfcheck.get("effective_days"),
        "change_points": len((change_points or {}).get("per_stance") or {}),
        "candidate_clusters": (candidates or {}).get("cluster_count") if candidates else None,
        "candidates_suspected_unnamed": ((candidates or {}).get("suspected_unnamed_count")
                                         if candidates else None),
        "drift_items": len((drift_report or {}).get("items") or []),
        "llm_agreement_pct": (((llm_checks or {}).get("confusion") or {}).get("agreement_pct")
                              if llm_checks else None),
        "irony_suspect_records": irony_total,
        "alerts": _alerts,
        "size_note": "本摘要供快速读取；明细见同文件其余字段与 opinion.md，勿整体读取 JSON",
    }
    read_hint = {
        "read_first": "python opinion.py --digest <本文件>（或读同目录 opinion.md）",
        "why": "本文件含明细（样本、诊断、聚类成员），体积随语料增长；整体读取会占用大量上下文",
        "detail_keys": ["stance_distribution[].top_samples", "residual.diagnostics",
                        "audience_structure", "stance_comparisons"],
        "machine_consumers": "百宝袋 build_report.py 按字段读（程序读不受影响）",
    }

    result = {
        # 摘要与读取提示放最前：任何读者先看到"该读哪里"
        "summary": summary,
        "_read_hint": read_hint,
        "event": args.event,
        "total_records": total,
        "denominator": denom,
        "coverage_rate": round(denom / total * 100, 1) if total else 0,
        "coverage_of_judgeable": round(denom / judgeable * 100, 1) if judgeable else 0,
        "judgeable_denominator": judgeable,
        # v0.5 新增（D9）：去掉规则层依赖的可判集合口径，跨报告可比
        "coverage_of_judgeable_norule": round(denom / judgeable_norule * 100, 1) if judgeable_norule else 0,
        "judgeable_norule_denominator": judgeable_norule,
        "judgeable_breakdown": judgeable_breakdown,
        "stance_distribution": dist,
        # v0.6 新增：人数口径、立场两两差异、精度与样本量需求、传播结构
        "author_distribution": author_distribution,
        "stance_comparisons": stance_comparisons,
        "precision": precision,
        "audience_structure": audience_structure,
        # v0.7 新增（E3.1/E3.2/E3.4/E3.5/E4.1/E4.2/E4.3/E4.5/E5.1/E5.2/E5.3）
        "stance_timeline_detail": stance_timeline_detail,
        "change_points": change_points,
        "event_alignment": event_alignment,
        "selfcheck": selfcheck,
        "candidates": candidates,
        "llm_checks": llm_checks,
        "vocabulary_advice": vocabulary_advice,
        "run_manifest": run_manifest,
        "drift_report": drift_report,
        "irony_note": {
            "suspect_records": irony_total,
            "signals": [name for name, _pat in _IRONY_PATTERNS],
            "note": "irony_suspect 只是**人工复核提示**（样本字段与计数），绝不自动改判；"
                    "被标记的记录会优先进 spotcheck 的富集块。",
        },
        "intensity_note": ({"levels": sorted(intensity_cfg), "note":
                            "强度档来自 stances.json 的 intensity 词表，只作描述维度，不进分母、不改占比。"}
                           if intensity_cfg else None),
        "institutional_layer": {
            "count": len(B),
            "pct_of_total": round(len(B) / total * 100, 1) if total else 0,
            "reasons": dict(inst_reason),
            "rule_set": {"name_suffixes": inst_cfg.get("name_suffixes"),
                         "name_words": inst_cfg.get("name_words"),
                         "name_regexes": inst_cfg.get("name_regexes"),
                         "repost_patterns": inst_cfg.get("repost_patterns") or []},
            "note": inst_cfg["note"],
        },
        "residual": {
            "count": len(C), "pct_of_total": round(len(C) / total * 100, 1) if total else 0,
            "split": {"机构帖未识别": sum(1 for r in C if is_institutional(r, inst_cfg)[0]),
                      "个人 UGC 未识别": len(unc),
                      # D12：不传 --total-raw 时是**未知**，输出 null 而不是 0。
                      # 旧写法 max(0, (args.total_raw or total) - total) 会把「没传参数」写成
                      # 「剔除 0 条」，读者据此以为上游没有做治理。
                      "语料治理阶段剔除的无关噪声": (max(0, int(args.total_raw) - total)
                                                     if args.total_raw is not None else None),
                      "语料治理阶段剔除的无关噪声_口径": ("null = 未提供 --total-raw（未知），非 0"
                                                          if args.total_raw is None else "--total-raw - total")},
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
                              "minority": len(rows) / len(R) * 100 < minority_threshold if R else False,
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
        # LLM 标注层（导入式，v0.5）：与规则层同构；不合并、不改分母
        "llm_layer": ({
            "labels_file": args.llm_labels,
            "rows_read": llm_rows,
            "count": len(L),
            "count_field": sum(1 for r in L if labels[id(r)][0] == OTHER),
            "pct_of_judgeable": round(len(L) / judgeable * 100, 1) if judgeable else 0,
            "distribution": [{"stance": s, "count": len(rows),
                              "pct": round(len(rows) / len(L) * 100, 1) if L else 0,
                              "sample": (rows[0].get("content") or "")[:120] if rows else ""}
                             for s, rows in sorted(llm_dist.items(), key=lambda kv: -len(kv[1]))],
            "status": "未抽检",
            "note": "llm 层是**导入的外部标注**（本脚本不联网、不调用模型）。它与词典层、规则层并列不合并："
                    "在人工抽检写出误判率之前，只能作为占比上界呈现，不得单独下判断。"
                    "启用该层会改变「哪条记录被判为什么」，故必须与词典层的数字并排披露。",
        } if llm_labels else None),
        "stance_timeline": {d: dict(day_stance[d]) for d in sorted(day_stance)},
        "timeline": dict(sorted(timeline.items())),
        "thresholds": {"coverage_target": coverage_target, "minority_threshold": minority_threshold,
                       "mid_samples": int(mid_samples or 0), "sources": threshold_src,
                       "config_source": cfg.get("_source"), "config_path": cfg.get("_path")},
        "pipeline": pipeline,
        "inference": {
            "proportion_ci": "wilson (95%%, z=%.3f)" % stats.Z95,
            "note": "每个立场的 pct_ci95 是 Wilson 得分区间：小样本与极端比例（p 接近 0/1）下比正态近似可靠，"
                    "而抽检样本量恰好常年落在那个区间。区间只反映抽样波动；绝对水平被系统性低估另有来源"
                    "（未识别层里的立场表达），须用 spotcheck 的未分类段估计并排披露。",
        },
        "note": "四层口径以 CONTRACT-joint §9.1 为唯一权威（本文件不复制口径文本，只列实现对应的层名）："
                "A 个人表达（立场分母）· B 机构帖（不进分母）· C 未识别残差 · D 治理剔除噪声。"
                "coverage_rate=A/全部语料（保守下界）；coverage_of_judgeable=A/可判集合"
                "（可判集合 = A + 规则层独有 + 未分类段，照 §9.1）；"
                "coverage_of_judgeable_norule=A/(A+未分类段)，去掉规则层依赖，跨报告比较用它。"
                "单标签聚类：命中关键词最多者胜，平票按词典插入顺序；少数派阈值见 thresholds；"
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
    # E5.2：词表建议补上"零命中"这一半（zero_hit 在 result 之后才算出来）
    if result.get("vocabulary_advice") is not None:
        result["vocabulary_advice"]["zero_hit_keywords"] = zero_hit
        result["vocabulary_advice"]["zero_hit_note"] = (
            "df=0 的词建议复核或删除（也可能是语料未覆盖）；改词表＝改口径，"
            "请同步 codebook 并用 `python vocab.py --diff 旧.json 新.json` 留痕。")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    base = os.path.splitext(args.out)[0]
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    lines = ["# 舆论立场聚类：%s" % args.event, "",
             "> 共 %d 条记录 ｜ 立场分母（个人表达）%d 条 ｜ 归类覆盖率 %.1f%%（占全部语料）"
             "｜可判集合口径 %.1f%%（跨报告可比 %.1f%%）"
             % (total, denom, result["coverage_rate"], result["coverage_of_judgeable"],
                result["coverage_of_judgeable_norule"]), "",
             "> 阈值来源：%s ｜ 管道模式：%s（层：%s）"
             % (json.dumps(threshold_src, ensure_ascii=False), pipeline["mode"],
                "、".join(pipeline["layers"])), ""]
    for d in dist:
        flag = " [少数派]" if d["minority"] else ""
        ci = d.get("pct_ci95") or [0, 0]
        lines.append("## %s（%.1f%% · 95%%CI %s%% · %d 条 · 赞同 %d）%s"
                     % (d["stance"], d["pct"], stats.fmt_ci(ci), d["count"], d["likes"], flag))
        for s in d["top_samples"]:
            tag = "  [命中: %s]" % "/".join(s["matched"]) if s["matched"] else ""
            neg = "  [⚠疑似否定]" if s.get("negated") else ""
            ref = "  [%s]" % s["ref_url"] if s.get("ref_url") else ""
            lines.append("- [赞%d] %s：%s%s%s%s" % (
                s["likes"], s["author"], s["content"].replace("\n", " ")[:120], tag, neg, ref))
        lines.append("")
    if audience_structure:
        _a = audience_structure["layers"]["A_个人表达"]
        lines.append("## 声量与人数（A 层）")
        lines.append("")
        lines.append("%d 条记录 / %d 位作者（条・人比 %.2f）｜HHI %.0f｜最高贡献账号占 %.1f%% 的记录｜"
                     "高频账号（≥%d 条）%d 个，贡献 %.1f%% 的记录"
                     % (_a["records"], _a["unique_authors"], _a["posts_per_author"],
                        _a["concentration"]["hhi_x10000"], _a["concentration"]["top1_share_pct"],
                        _a["heavy_accounts"]["threshold"], _a["heavy_accounts"]["accounts"],
                        _a["heavy_accounts"]["pct_of_records"]))
        lines.append("")
        for x in audience_structure["per_stance_by_author"]:
            _pct = ("（占立场作者 %.1f%%）" % x["pct_by_author"]) if x["pct_by_author"] is not None else ""
            lines.append("- %s：%d 条 / %d 人（每人 %.2f 条）%s"
                         % (x["stance"], x["records"], x["authors"], x["posts_per_author"] or 0, _pct))
        lines.append("")
        if author_distribution and author_distribution.get("by_main_stance"):
            lines.append("人数口径（按作者主立场）：" + "、".join(
                "%s %d 人（%.1f%%）" % (y["stance"], y["authors"], y["pct"])
                for y in author_distribution["by_main_stance"]))
            lines.append("")
        _dup = audience_structure["duplication"]
        lines.append("同源文本（SimHash 近重复，%s；只披露、不剔除、不改分母）：%d 条（%.1f%%）"
                     "落在 %d 个簇内，最大簇 %d 条；其中跨账号同源 %d 条（值得人工核）、"
                     "同账号重复 %d 条；规范化后同文 %d 条；未参与比较 %d 条（去链接/@/表情后过短）"
                     % (_dup["method"], _dup["near_duplicate_records"], _dup["near_duplicate_pct"],
                        _dup["cluster_count"], _dup["largest_cluster_size"],
                        _dup.get("cross_account_dup_records", 0),
                        _dup.get("same_author_repeat_records", 0),
                        _dup.get("exact_duplicate_records", 0),
                        _dup.get("not_compared_short_or_empty_text", 0)))
        lines.append("")
        _cb = audience_structure.get("clustered_by_author") or {}
        if _cb:
            lines.append("以作者为簇的占比修正（同一作者多条记录并不独立，独立口径会低估误差）："
                         + "；".join("%s %.1f%%（95%%CI %s%%，deff %s）"
                                     % (k, v["p_pct"], "–".join("%.1f" % z for z in v["ci95"]),
                                        v["design_effect"]) for k, v in _cb.items()))
            lines.append("")
    if stance_comparisons:
        lines.append("## 立场两两差异（同一批样本，多项口径；区间跨 0 即不得声称有差异）")
        lines.append("")
        for _c in stance_comparisons[:8]:
            lines.append("- %s vs %s：%+.1f 个百分点（95%%CI %.1f–%.1f）—— %s"
                         % (_c["pair"][0], _c["pair"][1], _c["diff_pp"],
                            _c["ci95_pp"][0], _c["ci95_pp"][1], _c["verdict"]))
        lines.append("")
    if precision.get("half_width_pp_at_p50") is not None:
        lines.append("## 精度")
        lines.append("")
        lines.append("分母 %d 条 → 占比 50%% 处的 95%% 区间半宽 ±%.1f 个百分点；"
                     "要达到 ±10pp 需分母约 %s 条，±5pp 约 %s 条（含有限总体校正）"
                     % (precision["denominator"], precision["half_width_pp_at_p50"],
                        precision["required_n_for_margin"]["±10pp"],
                        precision["required_n_for_margin"]["±5pp"]))
        lines.append("")
    # ---- v0.7：时序（每日分母+区间）、变点、走势闸门、事件对齐 ----
    if stance_timeline_detail:
        lines.append("## 逐日立场结构（分母 / 占比 / 95%CI / 作者）")
        lines.append("")
        for _day in sorted(stance_timeline_detail):
            v = stance_timeline_detail[_day]
            _parts = ["%s %s%%（95%%CI %s）"
                      % (s, v["pct"][s], "–".join("%.1f" % x for x in v["ci95"][s]))
                      for s in v["by_stance"]]
            lines.append("- %s：分母 %d（当日共 %d 条 / %d 人）｜%s"
                         % (_day, v["denominator"], v["records_total"], v["authors"], "；".join(_parts)))
        lines.append("")
    if selfcheck.get("trend_claim_forbidden"):
        lines.append("> ⚠ **不得谈走势**：%s。本报告不得出现「走势/转向/走高/回落」等表述。"
                     % "；".join(selfcheck.get("reasons") or []))
        lines.append("")
    for st, v in ((change_points or {}).get("per_stance") or {}).items():
        for x in (v.get("change_points") or [])[:3]:
            lines.append("- 变点候选（%s）：%s %s：%s%% → %s%%（差 %+.1fpp，95%%CI %.1f–%.1f）—— %s"
                         % (st, x["day"], x["direction"], x["before"]["pct"], x["after"]["pct"],
                            x["diff_pp"], x["diff_ci95_pp"][0], x["diff_ci95_pp"][1],
                            "强信号" if x["strong"] else "**弱信号：不得据此写转折**"))
    if (change_points or {}).get("per_stance"):
        lines.append("")
    if event_alignment:
        lines.append("## 事件对齐（仅时间相邻，不构成因果）")
        lines.append("")
        for e in event_alignment["events"]:
            lines.append("- %s %s（来源：%s）｜前 %d 条 → 后 %d 条"
                         % (e["event"]["time"], e["event"]["label"], e["event"]["source"],
                            e["before"]["records"], e["after"]["records"]))
            for st, s in (e.get("shifts") or {}).items():
                lines.append("  · %s：%s%% → %s%%（差 %s pp，95%%CI %.1f–%.1f）—— %s"
                             % (st, s["before_pct"], s["after_pct"], s["diff_pp"],
                                s["diff_ci95_pp"][0], s["diff_ci95_pp"][1], s["verdict"]))
        lines.append("")
    if candidates:
        lines.append("## 候选立场（构造词典的建议，**不是判定**）")
        lines.append("")
        lines.append("方法：%s｜共 %s 簇，疑似未命名 %s 个"
                     % (candidates.get("method"), candidates.get("cluster_count"),
                        candidates.get("suspected_unnamed_count")))
        for c in (candidates.get("clusters") or [])[:6]:
            lines.append("- %s（%s 条 / %s 位作者）%s｜高频表达：%s"
                         % (c.get("cluster"), c.get("size"), c.get("authors"),
                            "**疑似未命名立场**" if c.get("suspected_unnamed") else "",
                            "、".join(t["term"] for t in (c.get("top_terms") or [])[:5])))
        lines.append("")
    if llm_checks:
        lines.append("## LLM 层校验")
        lines.append("")
        lines.append("词典层×LLM 层一致率 %s%%（共同标注 %s 条）"
                     % ((llm_checks.get("confusion") or {}).get("agreement_pct"),
                        (llm_checks.get("confusion") or {}).get("n_labeled")))
        if llm_checks.get("model_agreement"):
            lines.append("双模型一致性：n=%s，κ=%s（%s）"
                         % (llm_checks["model_agreement"].get("n_pairs"),
                            llm_checks["model_agreement"].get("kappa"),
                            llm_checks["model_agreement"].get("reading")))
        for name, b in (llm_checks.get("confidence_bins") or {}).items():
            lines.append("- 置信度 %s：%s 条，与词典层一致 %s%%" % (name, b["n"], b["agree_pct"]))
        lines.append("")
    if drift_report:
        lines.append("## 口径漂移（--prev 比对）")
        lines.append("")
        if drift_report["items"]:
            for x in drift_report["items"]:
                lines.append("- %s：%s → %s（%s）"
                             % (x.get("item"), x.get("before"), x.get("after"),
                                x.get("why") or x.get("detail")))
        else:
            lines.append("- 无漂移：输入、词表、参数、脚本指纹均未变化")
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
    lines.append("跨报告可比口径（不受是否跑规则层影响）：可判集合 %d 条 → 覆盖 %.1f%%。"
                 "契约口径另有重叠计入问题（见 opinion.json 的 judgeable_breakdown）。"
                 % (judgeable_norule, result["coverage_of_judgeable_norule"]))
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
    if result.get("llm_layer"):
        ll = result["llm_layer"]
        lines.append("## LLM 标注层（导入式，与词典层并列不合并）")
        lines.append("")
        lines.append("命中 %d 条（其中词典层未识别的 %d 条）｜占可判集合 %.1f%%｜文件 %s"
                     % (ll["count"], ll["count_field"], ll["pct_of_judgeable"], ll["labels_file"]))
        for d in ll["distribution"]:
            lines.append("- %s：%d 条（%.1f%%）" % (d["stance"], d["count"], d["pct"]))
        lines.append("")
        lines.append("⚠ LLM 标注同样须人工抽检写出误判率后才可作为结论依据。")
        lines.append("")
    if terms_diag.get("available"):
        lines.append("词级高频词 Top20（jieba 增强，仅供构造词典）："
                     + "、".join("%s(%d)" % (x["word"], x["df"]) for x in terms_diag["terms"][:20]))
    else:
        lines.append("词级高频词（jieba 增强）：未启用——%s" % (terms_diag.get("reason") or "未请求"))
    lines.append("")
    lines.append("词表外高频词 Top20：" + "、".join("%s(%d)" % (x["gram"], x["df"])
                                                    for x in diff[:20]))
    lines.append("")
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("[opinion] 共 %d 条 → 词典层分母 %d 条（占全部语料 %.1f%% · 占可判集合 %.1f%% · "
          "跨报告可比口径 %.1f%%）｜机构帖 %d ｜未识别 %d ｜%d 个立场"
          % (total, denom, result["coverage_rate"], result["coverage_of_judgeable"],
             result["coverage_of_judgeable_norule"], len(B), len(C), len(dist)))
    print("[opinion] 未识别构成：%s（未识别 ≠ 与事件无关，相关性已在上游判过）" % seg)
    print("[opinion] 管道：mode=%s ｜层=%s ｜阈值来源=%s ｜config=%s"
          % (pipeline["mode"], "+".join(pipeline["layers"]),
             json.dumps(threshold_src, ensure_ascii=False), cfg.get("_source")))
    for _d in (pipeline.get("degraded") or []):
        print("[opinion] 增强降级：%s —— %s" % (_d.get("name"), _d.get("reason")))
    # D1 自检：真实事件语料里机构帖通常是小少数，B 层占比过高几乎一定是判定过宽
    if total and len(B) / total > 0.15:
        print("[opinion] ⚠ 机构帖占比 %.1f%%（>15%%）：请人工确认机构判定是否过宽——"
              "误判会把真实网民从立场分母里踢走（可用 --institutional 覆盖规则，或提交样本复核）"
              % (len(B) / total * 100))
    _cov_now = (denom / total * 100) if total else 0.0
    if _cov_now < float(coverage_target):
        print("[opinion] ⚠ 归类覆盖率 %.1f%% 低于目标 %.1f%%（阈值来源 %s）："
              "不得据此写「主流/压倒性」类强度表述；按 codebook 的止损规则处理"
              "（词典修订至多一次 → 仍不达标则走标注层路径并如实披露）"
              % (_cov_now, float(coverage_target), threshold_src["coverage_target"]))
    # 声量 vs 人数 + 精度：这两行决定"占比能不能直接读作公众分布"
    if audience_structure:
        _a = audience_structure["layers"]["A_个人表达"]
        print("[opinion] 声量 vs 人数（A 层）：%d 条 / %d 人（条 ∕ 人 %.2f）｜HHI %.0f ｜"
              "最高贡献账号 %.1f%% 的记录 ｜ 高频账号（≥%d 条）%d 个，占记录 %.1f%%"
              % (_a["records"], _a["unique_authors"], _a["posts_per_author"],
                 _a["concentration"]["hhi_x10000"], _a["concentration"]["top1_share_pct"],
                 _a["heavy_accounts"]["threshold"], _a["heavy_accounts"]["accounts"],
                 _a["heavy_accounts"]["pct_of_records"]))
        _dup = audience_structure["duplication"]
        if _dup["near_duplicate_records"]:
            print("[opinion] 近重复披露：%d 条（%.1f%%）落在 %d 个同源文本簇内（最大簇 %d 条）｜"
                  "跨账号同源 %d 条（值得人工核）· 同账号重复 %d 条｜未参与比较 %d 条（去链接/@/表情后过短）"
                  "——只披露，未剔除、未改分母"
                  % (_dup["near_duplicate_records"], _dup["near_duplicate_pct"],
                     _dup["cluster_count"], _dup["largest_cluster_size"],
                     _dup.get("cross_account_dup_records", 0), _dup.get("same_author_repeat_records", 0),
                     _dup.get("not_compared_short_or_empty_text", 0)))
        if author_distribution and author_distribution.get("by_main_stance"):
            print("[opinion] 人数口径（作者主立场）：%s"
                  % "、".join("%s %d 人(%.1f%%)" % (x["stance"], x["authors"], x["pct"])
                              for x in author_distribution["by_main_stance"]))
    if precision.get("half_width_pp_at_p50") is not None:
        print("[opinion] 精度：分母 %d 条 → 占比 50%% 处的 95%% 区间半宽 ±%.1f 个百分点；"
              "要到 ±10pp 需分母约 %s 条（含 fpc）"
              % (precision["denominator"], precision["half_width_pp_at_p50"],
                 precision["required_n_for_margin"]["±10pp"]))
    for _c in stance_comparisons[:3]:
        print("[opinion] 差异检验：%s vs %s = %+.1f 个百分点（95%%CI %.1f–%.1f）：%s"
              % (_c["pair"][0], _c["pair"][1], _c["diff_pp"], _c["ci95_pp"][0], _c["ci95_pp"][1],
                 _c["verdict"]))
    if selfcheck["trend_claim_forbidden"]:
        print("[opinion] ⚠ 不得谈走势（trend_claim_forbidden=true）：%s" % "；".join(selfcheck["reasons"]))
    else:
        print("[opinion] 走势闸门：通过（有效日 %d，跨度 %d 天）"
              % (selfcheck["effective_days"], selfcheck["days_with_stance"]))
    for _st, _v in ((change_points or {}).get("per_stance") or {}).items():
        for _x in (_v.get("change_points") or [])[:3]:
            print("[opinion] 变点候选：%s @ %s %s（%s%% → %s%%，%s）"
                  % (_st, _x["day"], _x["direction"], _x["before"]["pct"], _x["after"]["pct"],
                     "强信号" if _x["strong"] else "弱信号（区间跨 0，不得据此写转折）"))
    if candidates:
        if candidates.get("available", True):
            print("[opinion] 候选立场：%s 簇（疑似未命名 %s 个）"
                  % (candidates.get("cluster_count"), candidates.get("suspected_unnamed_count")))
        else:
            print("[opinion] 候选立场：未生效（%s）" % (candidates.get("reason") or "")[:80])
    if llm_checks:
        print("[opinion] LLM 校验：词典层×LLM 层一致率 %s%%（n=%s）%s"
              % ((llm_checks.get("confusion") or {}).get("agreement_pct"),
                 (llm_checks.get("confusion") or {}).get("n_labeled"),
                 "｜双模型 κ %s" % ((llm_checks.get("model_agreement") or {}).get("kappa"))
                 if (llm_checks.get("model_agreement") or {}).get("kappa") is not None else ""))
    if drift_report:
        if drift_report["items"]:
            for _x in drift_report["items"][:6]:
                print("[opinion] ⚠ 口径漂移：%s —— %s" % (_x.get("item"), _x.get("why") or _x.get("detail")))
        else:
            print("[opinion] --prev 比对：输入/词表/参数/脚本均未变化")
    if irony_total:
        print("[opinion] 反讽标记：%d 条疑似（只做复核提示，未自动改判）" % irony_total)
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
    try:
        _kb = os.path.getsize(base + ".json") / 1024.0
        print("[opinion] 工件 %.1f KB；读取请用 `python opinion.py --digest %s.json`（勿整体读取 JSON）"
              % (_kb, os.path.basename(base)))
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
