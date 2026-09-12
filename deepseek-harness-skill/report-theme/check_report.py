# -*- coding: utf-8 -*-
"""HTML 报告静态自检器。

用法：
    python check_report.py [--strict] [--json] [--quiet] 文件.html [文件2.html …]
    python check_report.py --joint [--strict] --data data/{event_id} 报告.html   # 联合任务 + 数据对账

规则清单（编号与判据）以 report-theme/CONTRACT.md §7 与 CONTRACT-joint 为准，本文件只做实现：
  通用 E1–E5 · W1–W16；联合 G1–G8（--joint）· 数据对账 R1–R4（--joint --data）。
  --data 是数据对账开关：不传则只查报告自身形态，查不出"数字互相矛盾 / 断言与语料不符"这一类问题。
  --allow <id,…>  显式豁免指定检查（须在方法论记录理由）。

退出码：有 error → 1；--strict 时 warning 亦计失败。0 = 通过。
"""
import argparse
import json
import re
import sys

HEADING = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.S)
REF_ID = re.compile(r'id="(ref\d+)"')
REF_CITE = re.compile(r'<a[^>]*href="#(ref\d+)"[^>]*class="ref"|<a[^>]*class="ref"[^>]*href="#(ref\d+)"')
REF_ROW = re.compile(r"<tr([^>]*id=\"ref\d+\"[^>]*)>")
TABLE_OPEN = re.compile(r"<table\b")
THEAD_OPEN = re.compile(r"<thead\b")


def _strip_tags_ish(s):
    return re.sub(r"<!--.*?-->", "", s, flags=re.S)


_JOINT_MANUAL = [
    "GW-W1 人工复核：背景/历史坐标（距今>1年）是否带时效核验标注（数据年份+其后变更检索+「系既往回应/非现状」）",
    "GW-W2 人工复核：断言登记册是否覆盖正文全部事实主张；未覆盖项是否有显式豁免清单（见覆盖声明）",
    "GW-W3 人工复核：「失实+证据不足」占比是否异常偏低（<20% 且断言≥10 为偏倚提示线）",
    "GW-W4 人工复核：方法论是否含查询词/轮次明细（可复现性，揽风云输出规范既有要求）",
]


def check_joint(text, fname="<string>"):
    """联合任务附加检查（CONTRACT-joint §7）：G1–G6。返回 issues 列表。"""
    issues = []

    def add(level, rid, msg):
        issues.append({"level": level, "id": rid, "line": 0, "msg": msg, "snippet": ""})

    if not re.search(r'<meta name="dsh-report"[^>]*content="joint', text):
        add("error", "G2", "缺联合 meta 标记（<meta name=\"dsh-report\" content=\"joint;…\">）："
                           "联合报告须由 build_report.py --joint 装配；手写 HTML 须在 <head> 自行声明")
    m = re.search(r'<table[^>]*class="[^"]*compliance[^"]*"[^>]*>(.*?)</table>', text, re.S)
    if not m:
        add("error", "G1", "缺 .compliance 合规账本表（build_report --compliance 渲染）：无法核验采集合规")
    else:
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S)
        for row in rows:
            cells = [re.sub(r"<[^>]+>", "", td).strip()
                     for td in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
            if not cells or cells[0] in ("平台",):
                continue
            if len(cells) < 6:
                add("error", "G1", "合规行[%s]字段不全（应 7 列）" % cells[0])
                continue
            status, uc = cells[1], cells[4]
            if status not in ("已采", "已采集", "跳过", "降级", "未采集"):
                add("error", "G1", "合规行[%s]状态非法：「%s」" % (cells[0], status))
            elif status in ("跳过", "降级", "未采集") and uc not in ("是", "已确认", "true", "True"):
                add("error", "G1", "合规行[%s]状态=%s 但用户确认=%s（须经用户确认，见 CONTRACT-joint §1/§4）"
                    % (cells[0], status, uc))
    chips = re.findall(r'<span class="v[^"]*"[^>]*>([^<]{0,24})</span>', text)
    flagged = [c for c in chips if any(mk in c for mk in "▲●△")]
    if flagged:
        add("error", "G3", "裁决 chip 混入确定性标记 ▲●△：%s（chip 只用裁决词；▲●△ 仅用于数字口径）"
            % " / ".join(flagged[:4]))
    thin_src = []
    for seg in _split_claims(text)[0]:
        mchip = re.search(r'<span class="v[^"]*"[^>]*>\s*([^<]*?)\s*</span>', seg)
        chip = (mchip.group(1) if mchip else "").strip()
        mtxt = re.search(r'<p class="claim-txt">(.*?)</p>', seg, re.S)
        ctxt = re.sub(r"<[^>]+>", "", mtxt.group(1)) if mtxt else ""
        mev = re.search(r"证据等级[:：]\s*([^<\n]{0,24})", seg)
        evl = mev.group(1) if mev else ""
        is_true = chip == "真实" or "报道属实" in chip
        if is_true and "原文未核" not in chip and any(k in evl for k in ("转载", "摘要", "标题层")):
            add("error", "G4", "断言「%s…」chip=%s 但证据等级=「%s」≤转载层（应显示「报道属实（原文未核）」）"
                % (ctxt[:24], chip or "真实", evl))
        if is_true and re.search(r"未见|未回应|未表态|未答复|未直接回应|并未回应|没有回应|尚未回应"
                                 r"|未就[^，。；]{0,15}?(?:回应|表态|答复|发声)", ctxt):
            add("error", "G5", "断言「%s…」含否定性表述却裁决为「真实」：否定性主张默认「证据不足」，"
                               "除非有权威渠道的明确否定声明；若该断言的正面部分（媒体报道层级等）"
                               "才是裁决对象，应把否定性从句从断言中拆出单独登记"
                               "（合并强弱不等的子主张见 CONTRACT-joint §6；确有异议用 --allow G5 并记录理由）"
                % ctxt[:40])
        # ---- G4b 独立信源数 <2（去重后）----
        if is_true:
            nref = len({(a or b) for a, b in REF_CITE.findall(seg) if (a or b)})
            if nref and nref < 2:
                thin_src.append(ctxt[:20])
    if len(thin_src) >= 3:
        add("warning", "G4b", "有 %d 条断言 chip=真实 但独立信源 <2（去重后 1 个）：%s…（"
                              "同一来源被多条断言反复引用时「多源交叉」不成立，见 CONTRACT-joint §2/§10）"
            % (len(thin_src), "、".join(thin_src[:5])))
    # E6 断言可溯源（联合模式）：第三章断言必须带引用编号
    m3 = re.search(r"<h2[^>]*>三、核心事实汇编</h2>(.*?)(?=<h2[^>]*>四、)", text, re.S)
    if m3 and 'class="claim"' in m3.group(1) and not re.search(r'class="ref"', m3.group(1)):
        add("error", "E6", "第三章断言无任何引用编号：断言必须可溯源（facts.json claims[].ref 或 "
                           "evidence[].ref → 来源索引锚点）")

    # G7 分母口径披露（联合模式）：立场占比须与归类覆盖率同时呈现，且覆盖率低时不得据此下重结论
    if 'class="bar-label"' in text:
        if not re.search(r"归类覆盖率", text):
            add("error", "G7", "缺「归类覆盖率」披露：立场占比必须与口径同时呈现（opinion.coverage_rate），"
                               "否则读者无法判断分母代表多少语料")
        else:
            mcov = re.search(r"归类覆盖率[^0-9]{0,12}(\d+(?:\.\d+)?)\s*%", text)
            mjud = re.search(r"占可判集合[^0-9]{0,12}(\d+(?:\.\d+)?)\s*%", text) or \
                re.search(r"可判集合[^0-9]{0,20}?(\d+(?:\.\d+)?)\s*%", text)
            vals = [float(mcov.group(1))] if mcov else []
            if mjud:
                vals.append(float(mjud.group(1)))
            # 用"较大"的覆盖率判强度断言：两个口径都低才算真的低；
            # 报告不得靠挑一个窄分母来给"压倒性"背书，也不该因保守分母而被误伤。
            if vals and max(vals) < 30:
                mclaim = re.search(r"压倒性|压倒多数|舆论主体|主流立场(?:在|是)", text)
                if mclaim:
                    add("error", "G7", "归类覆盖率 %s 均 <30%% 却出现强度断言「%s」："
                                       "占比只读作「可识别立场内部的相对结构」，"
                                       "不得使用主流/压倒性类措辞（CONTRACT-joint §9.1）"
                        % ("、".join("%g%%" % v for v in vals), mclaim.group(0)))

    # G9 图例与渲染一致：图例声称的节点类型/日计数必须在正文真的出现
    if 'class="tl-item"' in text:
        m_tl = re.search(r'<div class="tl"[^>]*>(.*?)</div>\s*<p class="legend">', text, re.S)
        tl_body = m_tl.group(1) if m_tl else ""
        for mleg in re.findall(r'<p class="legend">(.*?)</p>', text, re.S):
            if "节点类型" not in mleg and "日计数" not in mleg:
                continue
            cn2en = {"官方": "official", "媒体": "media", "解读": "view",
                     "背景": "bg", "收尾": "quiet", "核查": "check"}
            missing = [cn for cn, en in cn2en.items()
                       if cn in mleg and ("tg-" + en) not in tl_body]
            if missing:
                add("warning", "G9", "时间线图例声明了未渲染的节点类型：%s（图例与渲染不一致，"
                                     "读者会以为报告漏了节点；应删图例或补节点）" % "、".join(missing))
            if re.search(r"日计数|当日.*采集|采集记录数", mleg) and 'class="tl-cnt"' not in tl_body:
                add("warning", "G9", "时间线图例声称「日计数/采集记录数」，但时间线没有任何采集量胶囊："
                                     "图例只说实际渲染了的东西")

    # G10 残差语义（v0.4）：未命中词典 ≠ 与事件无关；残差必须拆段披露，且占比须给可判集合分母
    if re.search(r"无关/其他|未归类残差|未识别", text):
        if re.search(r"无关/其他", text):
            add("error", "G10", "报告仍使用「无关/其他」命名未命中词典的语料：该词会被读成与事件无关，"
                                "而相关性已在上游语料治理判过。应改为「未识别」，并分段披露"
                                "（纯反应 / 原样转述 / 未分类）")
        if 'class="bar-label"' in text:
            has_seg = bool(re.search(r"纯反应|待语义判读|原样转述|不含立场表达", text))
            if not has_seg:
                add("error", "G10", "未识别残差未拆段披露：占比旁的残差说明必须给出分段构成"
                                    "（纯反应 / 原样转述 / 待语义判读），否则读者无法判断"
                                    "「未识别」里到底有多少是不表态的闲聊")
            if re.search(r"归类覆盖率[^0-9]{0,12}\d", text) and not re.search(r"可判集合", text):
                add("error", "G10", "占比只给了「占全部语料」的覆盖率，缺「占可判集合」口径："
                                    "全部语料分母含大量本就不含立场表达的反应/玩梗/轶事，"
                                    "会把覆盖率系统性压低（CONTRACT-joint §9.1）")

    # G11 人工抽检（v0.4）：占比必须带抽检结论，否则只是一个未校正的裸数字
    if re.search(r'class="bar-label"', text):
        if not re.search(r"抽检|误判率", text):
            add("error", "G11", "报告缺人工抽检结论：占比必须与抽检误判率并排呈现（spotcheck.py），"
                                "否则读者无法判断这个百分比有多结实（CONTRACT-joint §9.1）")
        else:
            mrate = re.findall(r"误判率[^0-9%]{0,12}(\d+(?:\.\d+)?)\s*%", text)
            mbad = re.search(r"不得作为主结论|误判率\s*>\s*30|不可（", text)
            hi = [float(x) for x in mrate if float(x) > 30]
            if hi and not mbad:
                add("error", "G11", "存在误判率 >30%% 的立场（%s）却未标注「不得作为主结论」："
                                    "抽检发现不可靠的立场必须显式降级（CONTRACT-joint §9.1）"
                    % "、".join("%g%%" % x for x in hi))
            if not re.search(r"未识别|未分类", text):
                add("error", "G11", "缺未识别层抽样结论：未识别层须给出「含立场表达」的估计"
                                    "（含 95% 区间），否则读者不知道占比被低估了多少")

    # G8 剔除披露：语料治理剔除量必须带数字与来源构成，不能只出现「剔除」两个字
    m7 = re.search(r"<h2[^>]*>[^<]*覆盖完整性声明[^<]*</h2>(.*?)(?=<h2)", text, re.S)
    if m7:
        cov = re.sub(r"<[^>]+>", " ", m7.group(1))
        has_num = re.search(r"剔除[^。；\n]{0,60}?(\d{1,6})\s*条", cov)
        has_comp = re.search(r"(微博|小红书|知乎)[^。；\n]{0,40}(?:剔除|无关|过滤|剔除|不相关)"
                             r"|(?:剔除|无关|过滤)[^。；\n]{0,40}(微博|小红书|知乎)", cov)
        if not has_num:
            add("error", "G8", "覆盖声明未给出语料治理剔除量：须写明剔除记录数、占原始采集数的比例与构成"
                               "（哪一平台、帖文还是评论）。只出现「剔除」二字不算披露——"
                               "否则「无跳过、无降级」会掩盖大规模剔除（CONTRACT-joint §4）")
        elif not has_comp:
            add("warning", "G8", "覆盖声明写了剔除量但未给构成（哪一平台、帖文还是评论）："
                                 "剔除集中在单一平台时，占比的分母含义会随之改变（CONTRACT-joint §4）")
    mabs = re.search(r'<div class="abstract">(.*?)</div>\s*<div class="kpis"', text, re.S)
    if mabs and m7:
        mdel = re.search(r"剔除[^。；]{0,40}?(\d+(?:\.\d+)?)\s*%", m7.group(1))
        if mdel and float(mdel.group(1)) > 30 and not re.search(r"剔除|无关|过滤", mabs.group(1)):
            add("error", "G8", "语料剔除占比 %s%% >30%% 但摘要未提：剔除规模属读者判断占比可信度的前提，"
                               "须与占比同时出现（CONTRACT-joint §4）" % mdel.group(1))

    if re.search(r"进行中|仍在(发酵|持续)|尚未平息", text):
        m5 = re.search(r"<h2[^>]*>[^<]*舆论观点综合[^<]*</h2>(.*?)(?=<h2)", text, re.S)
        trend = m5.group(1) if m5 else text
        mdet = re.search(r"回落|趋于平息|即将平息|热度消退|已(?:经)?结束|尘埃落定", trend)
        if mdet:
            add("error", "G6", "事件状态=进行中 但舆论章出现确定性走势表述「%s」（应改情景句，见 CONTRACT-joint §5；"
                               "确有异议用 --allow G6 并记录理由）" % mdet.group(0))
    return issues


def _split_claims(text):
    """把报告切成断言段与立场卡段；段尾用 </article> 收口，避免相邻段互相"借"引用与 chip。"""
    claims, scards = [], []
    for seg in re.split(r"(?=<article class=\"claim\">)", text):
        if 'class="claim"' in seg:
            claims.append(seg.split("</article>")[0])
    for seg in re.split(r"(?=<article class=\"scard\">)", text):
        if 'class="scard"' in seg:
            scards.append(seg.split("</article>")[0])
    return claims, scards


def check_artifacts(data_dir):
    """②③工件体检（CONTRACT-joint §11）：G12（②）+ G13（③）。

    ②揽风云交出的四个工件此前无 schema，只能靠模型即兴发挥——实测产出过
    "时间线 08-26 无年份 / 图例声称日计数却不渲染 / 来源分级 A=0 / 覆盖声明漏剔除量"。
    ③破虚妄的 facts.json 此前**根本没人查**——实测产出过"口径冲突因形态不同被装配端
    整段丢弃 / 证据数组里写「反证：」「检索未见」当来源 / 否定性主张正文与标题反向 /
    一条断言合并四五件可分别裁决的事 / ref 编号越出 sources.json 编号域"，全流程零报警。
    本检查把"②③有没有按契约交工件"变成可机判项。
    """
    import os
    issues = []

    def add(level, rid, msg):
        issues.append({"level": level, "id": rid, "line": 0, "msg": msg, "snippet": ""})

    if not os.path.isdir(data_dir):
        return issues

    # ---- G12a timeline.json：日期一律 YYYY-MM-DD ----
    p = os.path.join(data_dir, "timeline.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8-sig") as f:
                tl = json.load(f)
        except ValueError:
            add("error", "G12", "timeline.json 不是合法 JSON")
            tl = None
        if isinstance(tl, list):
            bad = []
            for m in tl:
                if not isinstance(m, dict):
                    continue
                for tok in re.findall(r"\d{4}-\d{2}-\d{2}|\d{2}-\d{2}", str(m.get("date", ""))):
                    if len(tok) == 5:
                        bad.append(tok)
            if bad:
                add("error", "G12", "timeline.json 有 %d 处日期缺年份（如 %s）："
                                    "契约要求一律 YYYY-MM-DD（区间两端带年）（CONTRACT-joint §11）"
                    % (len(bad), bad[0]))
            miss = [k for k in ("date", "type", "tag", "title", "text")
                    if not all(k in (m or {}) for m in tl if isinstance(m, dict))]
            if miss:
                add("warning", "G12", "timeline.json 有节点缺必填字段：%s" % "、".join(miss))

    # ---- G12c sources.json：字段齐备 + 分级合法 + 归档文件存在 ----
    p = os.path.join(data_dir, "sources.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8-sig") as f:
                srcs = json.load(f)
        except ValueError:
            add("error", "G12", "sources.json 不是合法 JSON")
            srcs = None
        if isinstance(srcs, list):
            n_nograd = sum(1 for s in srcs if str((s or {}).get("grade", "")).upper() not in ("A", "B", "C"))
            if n_nograd:
                add("error", "G12", "sources.json 有 %d 条缺合法 grade（须 A|B|C，口径见 §2）" % n_nograd)
            n_nourl = sum(1 for s in srcs if not str((s or {}).get("url", "")).strip())
            if n_nourl:
                add("warning", "G12", "sources.json 有 %d 条缺 url" % n_nourl)
            missing_files = []
            for s in srcs:
                ff = str((s or {}).get("fetched_file", "")).strip()
                if ff and not os.path.exists(os.path.join(data_dir, "sources", ff)):
                    missing_files.append(ff)
            if missing_files:
                add("error", "G12", "sources.json 声明的归档文件不存在：%s（sources/ 下未找到）"
                    % "、".join(missing_files[:4]))
            grades = [str((s or {}).get("grade", "")).upper() for s in srcs]
            if grades and grades.count("A") == 0:
                add("warning", "G12", "来源分级 A=0：若正文引用了官媒原文或当事人账号原文，"
                                      "说明分级未落地（转载稿应按原始出处定级，见 §2）")
            n_archived = sum(1 for s in srcs if str((s or {}).get("fetched_file", "")).strip())
            if n_archived == 0:
                add("warning", "G12", "sources.json 无任何 fetched_file：抓取的正文与来源编号无法对应，"
                                      "归档目录不可回溯（§11）")

    # ---- G12d coverage.md：必含语料口径 ----
    p = os.path.join(data_dir, "coverage.md")
    if os.path.exists(p):
        with open(p, encoding="utf-8", errors="replace") as f:
            cov = f.read()
        if not re.search(r"剔除", cov):
            add("error", "G12", "coverage.md 缺语料口径小节：须写原始采集 → 分析语料 → 剔除量/占比/构成"
                                "（CONTRACT-joint §11）")

    # ================= G13 ③工件体检（facts.json）=================
    VERDICTS = ("真实", "部分真实", "失实", "证据不足")
    NEG = r"未见|未回应|未表态|未发生|未收到|未检索到|尚无|尚未|没有|并未|未曾|无公开"
    INFER = r"可用以证明|足以证明|由此可见|这说明|说明其|可以证明"
    p = os.path.join(data_dir, "facts.json")
    facts = None
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8-sig") as f:
                facts = json.load(f)
        except ValueError:
            add("error", "G13", "facts.json 不是合法 JSON")
    if isinstance(facts, dict):
        claims = [c for c in (facts.get("claims") or []) if isinstance(c, dict)]
        if not claims:
            add("error", "G13", "facts.json 无 claims[]：断言登记册为空，第三章无法溯源（§11）")
        n_src = 0
        sp = os.path.join(data_dir, "sources.json")
        if os.path.exists(sp):
            try:
                with open(sp, encoding="utf-8-sig") as f:
                    _s = json.load(f)
                n_src = len(_s) if isinstance(_s, list) else 0
            except ValueError:
                n_src = 0
        if n_src == 0:
            # 不静默跳过：缺 sources.json 时 ref 编号域无从校验，这件事本身要说出来
            add("warning", "G13", "缺 sources.json（或为空）：断言 ref 的编号域无法校验——"
                                  "引用锚点是否指向正确来源属未核查项（§11）")
        ref_over, compound, inferential, weak_title = [], [], [], []
        for c in claims:
            cid = str(c.get("id") or "?")
            st = str(c.get("short_title") or "").strip()
            raw = str(c.get("claim") or "")
            # a. 必填字段
            miss = [k for k in ("id", "claim", "short_title", "verdict", "evidence_level", "reason")
                    if not str(c.get(k) or "").strip()]
            if not (c.get("evidence") or []):
                miss.append("evidence")
            if miss:
                add("error", "G13", "断言 %s 缺必填字段：%s（schema 见 CONTRACT-joint §11）"
                    % (cid, "、".join(miss)))
            # b. 裁决词只用四个（禁止「真实（观点性判断）」这类后缀）
            v = str(c.get("verdict") or "").strip()
            if v and v not in VERDICTS:
                add("error", "G13", "断言 %s 的裁决词「%s」不在四个合法词内（真实/部分真实/失实/证据不足）："
                                    "观点性主张不进断言登记册，应归观点辨析（§3/§6）" % (cid, v))
            # c. 证据等级
            ev = str(c.get("evidence_level") or "").strip()
            if ev and ev not in ("1", "2", "3", "4") and not re.search(r"官方|权威|转载|摘要|标题层", ev):
                add("warning", "G13", "断言 %s 的证据等级「%s」不可识别（应为 1|2|3|4，§2）" % (cid, ev))
            # d. 证据数组只放支撑证据
            for item in (c.get("evidence") or []):
                s = str((item or {}).get("source") or "") if isinstance(item, dict) else str(item)
                if re.match(r"\s*(反证|检索未见|现状|缺失|未检索到|无公开)", s):
                    add("error", "G13", "断言 %s 的证据数组里写了非证据条目「%s…」：反证与「检索未见」"
                                        "属裁决说明，写 reason；证据数组只放支撑证据（§11）" % (cid, s[:18]))
            # e. 否定性主张同向
            if re.search(NEG, st) and not re.search(NEG, raw):
                add("error", "G13", "断言 %s 短标题含否定表述（%s）而正文为肯定句：否定性主张必须以"
                                    "否定句登记，否则同一张卡标题/正文/裁决三向相反（§3）"
                    % (cid, re.search(NEG, st).group(0)))
            # f. ref 编号域
            for tok in re.findall(r"\[(\d{1,3})\]", str(c.get("ref") or "") +
                                  "".join(str((e or {}).get("ref") or "")
                                          for e in (c.get("evidence") or []) if isinstance(e, dict))):
                if n_src and int(tok) > n_src:
                    ref_over.append("%s→[%s]" % (cid, tok))
            # g. conflicts 形态（装配端只认 §11 那一种；别的写法会被静默丢弃）
            cf = c.get("conflicts")
            if isinstance(cf, dict) and not (("point" in cf and "versions" in cf)
                                             or "a" in cf or "b" in cf or "issue" in cf):
                add("error", "G13", "断言 %s 的 conflicts 用了装配端不识别的形态（键：%s）："
                                    "会被静默丢弃，口径冲突整段消失（§11）"
                    % (cid, "、".join(list(cf.keys())[:4])))
            # h. 复合断言（判据：可分别裁决的子句被合并；列条待人工确认）
            semi = raw.count("；")
            seps = raw.count("，") + raw.count("、")
            # 先去掉日期里的数字：否则"2026年9月9日刊发评论，批评……"这类单事实断言
            # 会因为日期里挤着一串数字而被误判（实测 27 条里误报 1 条）。
            body = re.sub(r"\d{4}\s*年|\d{1,2}\s*月\d{1,2}\s*日|\d{1,2}\s*时|\d{4}-\d{2}-\d{2}", "", raw)
            digits = len(re.findall(r"\d", body))
            if (semi >= 1 and seps >= 2) or (digits >= 2 and "、" in raw and "，" in raw):
                compound.append("%s「%s」" % (cid, st[:14]))
            # i. 推论被当事实登记
            if re.search(INFER, raw):
                inferential.append("%s「%s」" % (cid, st[:14]))
            # j. 短标题长度
            if len(st) > 24:
                weak_title.append("%s(%d字)" % (cid, len(st)))
        if ref_over:
            add("error", "G13", "断言 ref 编号越出 sources.json 编号域（共 %d 条来源）：%s"
                                "（ref 必须落在 1..%d，否则锚点指向错误来源，§11）"
                % (n_src, "、".join(ref_over[:5]), n_src))
        if compound:
            add("warning", "G13", "有 %d 条断言疑似合并了多个可分别裁决的子主张：%s…"
                                  "（一条断言只承载一个可独立裁决的主张，须拆条或人工确认，§6）"
                % (len(compound), "、".join(compound[:4])))
        if inferential:
            add("warning", "G13", "有 %d 条断言含证明力评价（「可用以证明/由此可见」类）：%s…"
                                  "（证明力判断不进断言登记册，归观点辨析，§6）"
                % (len(inferential), "、".join(inferential[:3])))
        if weak_title:
            add("warning", "G13", "有 %d 条断言 short_title 超过 24 字：%s" % (len(weak_title),
                                                                              "、".join(weak_title[:4])))
        # k. 观点辨析（verify 第 6 章的联合态落点）
        oa = [x for x in (facts.get("opinion_analysis") or []) if isinstance(x, dict)]
        if not oa:
            add("warning", "G13", "facts.json 无 opinion_analysis：报告第五章「观点辨析与推理说明」将为空。"
                                  "若本轮确有可辨析的论证结构（隐含前提/逻辑谬误/立场偏差），应补登（§11）")
        else:
            for k, it in enumerate(oa, 1):
                m2 = [f for f in ("target", "premise", "chain", "conclusion")
                      if not str(it.get(f) or "").strip()]
                if m2:
                    add("warning", "G13", "观点辨析 #%d（%s）缺字段：%s（§11）"
                        % (k, str(it.get("id") or "V%d" % k), "、".join(m2)))
    return issues


def check_joint_data(text, data_dir, fname="<string>"):
    """数据对账（CONTRACT-joint §10）：把报告与 data/{event_id}/ 下工件对账。R1–R4。

    只在 --joint --data 时启用。逐条对应契约 §10 的四条硬约束：
      R1 一数一源：同一指标多次出现须同值
      R2 时效一致：摘要天数与 KPI 时间跨度须与时间线首末节点相容
      R3 语料窗口：分析语料不得含早于事件起点的记录
      R4 证据不足须向语料反查：裁决理由不得与语料规模明显不符
    """
    import datetime
    import os
    issues = []

    def add(level, rid, msg):
        issues.append({"level": level, "id": rid, "line": 0, "msg": msg, "snippet": ""})

    def load(name):
        p = os.path.join(data_dir, name)
        if not os.path.exists(p):
            return None
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return None

    if not os.path.isdir(data_dir):
        add("error", "R0", "--data 指向的目录不存在：%s（数据对账无法执行，规则见 CONTRACT-joint §10）" % data_dir)
        return issues

    # ---------- 语料（R3 / R4 共用）----------
    # 固定文件名优先（百宝袋阶段 2 约定），兼容历史目录里的各种别名
    corpus = []
    raw_jsonl = None
    for cand in (os.path.join("normalized", "data.relevant.jsonl"),
                 os.path.join("normalized", "data_filtered.jsonl"),
                 os.path.join("normalized", "data_relevant.jsonl"),
                 "data_filtered.jsonl", "data.relevant.jsonl"):
        raw_jsonl = load(cand)
        if raw_jsonl is not None:
            break
    if raw_jsonl is None:
        add("error", "R3", "找不到分析语料：需 normalized/data.relevant.jsonl（标准名，见 CONTRACT-joint §10）；"
                           "语料窗口与断言反查无法执行")
    else:
        for ln in raw_jsonl.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                corpus.append(json.loads(ln))
            except ValueError:
                continue

    # ---------- R2 时效一致 ----------
    tl = None
    raw_tl = load("timeline.json")
    if raw_tl:
        try:
            tl = json.loads(raw_tl)
        except ValueError:
            tl = None
    ev_start = ev_end = None
    tl_dates = []         # 时间线节点日期（MM-DD 或 YYYY-MM-DD）
    if isinstance(tl, dict):
        for v in tl.values():
            items = v if isinstance(v, list) else [v]
            for it in items:
                d = it.get("date") if isinstance(it, dict) else None
                if d:
                    for tok in re.findall(r"\d{4}-\d{2}-\d{2}|\d{2}-\d{2}", str(d)):
                        tl_dates.append(tok)
    elif isinstance(tl, list):
        for it in tl:
            d = it.get("date") if isinstance(it, dict) else None
            if d:
                for tok in re.findall(r"\d{4}-\d{2}-\d{2}|\d{2}-\d{2}", str(d)):
                    tl_dates.append(tok)
    if tl_dates:
        ev_start, ev_end = min(tl_dates), max(tl_dates)
    if ev_start is None and corpus:
        ds = sorted(str(r.get("time", ""))[:10] for r in corpus if re.match(r"^\d{4}-\d{2}-\d{2}", str(r.get("time", ""))))
        if ds:
            ev_start, ev_end = ds[0], ds[-1]
    # R3 需要的是"事件本身"的起点：优先用事件时间线首节点（build_report 也据此渲染 KPI 跨度），
    # 否则退化为"事件月"（语料记录最多的月份）内最早的一天。
    corpus_days = sorted({str(r.get("time", ""))[:10] for r in corpus
                          if re.match(r"^\d{4}-\d{2}-\d{2}", str(r.get("time", "")))})
    corpus_start, corpus_end = (corpus_days[0], corpus_days[-1]) if corpus_days else ("", "")
    if corpus:
        months = {}
        for r in corpus:
            t = str(r.get("time", ""))
            if re.match(r"^\d{4}-\d{2}", t):
                months[t[:7]] = months.get(t[:7], 0) + 1
        if months:
            main_month = max(months.items(), key=lambda kv: kv[1])[0]
            in_month = sorted(str(r.get("time", ""))[:10] for r in corpus
                              if str(r.get("time", ""))[:7] == main_month)
            if in_month:
                # 事件起点 = 时间线首节点（可能是月初/跨月）与语料同月最早日的**较早者**
                ev_start = min([d for d in (ev_start, in_month[0]) if d])
                if not ev_end or ev_end[:7] == main_month:
                    ev_end = in_month[-1]
    if ev_start is None and corpus:
        ds = sorted(str(r.get("time", ""))[:10] for r in corpus if re.match(r"^\d{4}-\d{2}-\d{2}", str(r.get("time", ""))))
        if ds:
            ev_start, ev_end = ds[0], ds[-1]
    # R3 需要的是"事件本身"的起点，而非背景/历史坐标：取事件月（语料记录最多的月份）内最早的一天
    if corpus:
        months = {}
        for r in corpus:
            t = str(r.get("time", ""))
            if re.match(r"^\d{4}-\d{2}", t):
                months[t[:7]] = months.get(t[:7], 0) + 1
        if months:
            main_month = max(months.items(), key=lambda kv: kv[1])[0]
            in_month = sorted(str(r.get("time", ""))[:10] for r in corpus
                              if str(r.get("time", ""))[:7] == main_month)
            if in_month:
                ev_start = in_month[0]

    def _days(a, b):
        if not (re.match(r"^\d{4}-\d{2}-\d{2}$", a) and re.match(r"^\d{4}-\d{2}-\d{2}$", b)):
            return None
        return abs((datetime.date(*[int(x) for x in b.split("-")]) -
                    datetime.date(*[int(x) for x in a.split("-")])).days)

    # ① 同一报告声明的区间与天数须自洽（如摘要"共 16 天" vs KPI"2024-05-13 ~ 2026-09-10"）
    mspan = re.search(r"(\d{4}-\d{2}-\d{2})\s*[~～至\-—]\s*(\d{4}-\d{2}-\d{2})", text)
    mdur = re.search(r"(?:共|跨度|历时)\s*(\d+)\s*天", text)
    if mspan:
        try:
            real = _days(mspan.group(1), mspan.group(2))
            if mdur and abs(real - int(mdur.group(1))) > max(3, real * 0.2):
                add("error", "R2", "报告声明的区间（%s ~ %s，实为 %d 天）与「共 %s 天」自相矛盾："
                                   "跨度须由事件时间线计算，不得取全语料 min/max（CONTRACT-joint §10.1）"
                    % (mspan.group(1), mspan.group(2), real, mdur.group(1)))
            # 语料本身越窗 → 报告声明区间必然不可核对。只判"语料里真有窗内早期记录"的情形，
            # 否则语料恰好在事件起点后才被采到（合法）会误报。
            pre_in_window = [d for d in corpus_days
                             if corpus_start and corpus_start < d < mspan.group(1)] \
                if mspan.group(1) > corpus_start else []
            if mspan.group(1) < corpus_start and pre_in_window:
                add("error", "R2", "语料含 %d 个早于报告声明起点（%s）的记录日（%s…）："
                                   "声明起点与语料不一致，事件跨度须由时间线计算"
                    % (len(pre_in_window), mspan.group(1), pre_in_window[0]))
            elif mspan.group(1) < corpus_start and _days(corpus_start, mspan.group(1)) and \
                    _days(corpus_start, mspan.group(1)) > 30:
                add("error", "R2", "报告声明的区间起点 %s 早于语料最早记录 %s 达 %d 天："
                                   "事件跨度不得取全语料 min/max，背景/历史坐标应单列，"
                                   "不得进入 KPI（CONTRACT-joint §10.1）"
                    % (mspan.group(1), corpus_start, _days(corpus_start, mspan.group(1))))
        except (ValueError, TypeError):
            pass
    # ② KPI 天数须与事件窗口相容
    for m in re.finditer(r"(\d+)\s*<span>天</span>", text):
        if ev_start and ev_end:
            real = _days(ev_start, ev_end)
            if real is None:
                continue
            stated = int(m.group(1))
            if abs(real - stated) > 3 and (not mdur or stated != int(mdur.group(1)) or not mspan):
                add("error", "R2", "KPI 时间跨度写「%s 天」与事件窗口（%s ~ %s，实为 %d 天）不符"
                    % (m.group(1), ev_start, ev_end, real))

    # ---------- R3 语料窗口 ----------
    if corpus and ev_start:
        early = [r for r in corpus
                 if re.match(r"^\d{4}-\d{2}-\d{2}", str(r.get("time", ""))) and str(r["time"])[:10] < ev_start]
        if early:
            plats = {}
            for r in early:
                plats[r.get("platform", "?")] = plats.get(r.get("platform", "?"), 0) + 1
            add("error", "R3", "分析语料含 %d 条早于事件起点（%s）的记录（%s）："
                               "评论须按自身时间过滤，不得只按父帖相关度放行；"
                               "应剔除后重新聚类并披露剔除数（CONTRACT-joint §10.2）"
                % (len(early), ev_start, "、".join("%s %d 条" % kv for kv in sorted(plats.items()))))

    # ---------- R1 一数一源（报告内部同值）----------
    # 只看「平台名 … 数字 + 量词」的命名数字（条/个问题/个回答），忽略标题编号等无关数字；
    # 同一平台出现两个不同记录数即报错——不同口径（原始量 / 相关量 / 剔除量）必须逐处写明。
    corpus_text = re.sub(r'<div class="abstract">.*?</div>\s*<div class="kpis"', "", text, flags=re.S)
    # 只看正文（去掉来源索引章与附录：那里有平台名 + 引用编号，不是记录数）
    cut = corpus_text.find("来源索引</h2>")
    corpus_text = corpus_text[:cut] if cut > 0 else corpus_text
    for plat in ("微博", "小红书", "知乎"):
        vals = {}
        for m in re.finditer(plat, corpus_text):
            mtok = re.search(r"(\d{1,6})\s*条", corpus_text[m.end():m.end() + 20])
            if not mtok:
                continue          # 「N 个问题 / N 个回答」不是记录数，不计
            vals[mtok.group(1)] = vals.get(mtok.group(1), 0) + 1
        if len(vals) > 1:
            add("error", "R1", "同一平台「%s」在报告内出现多个记录数 %s（单位均为条）：一数一源，"
                               "同一指标须同值；若为不同口径（原始采集量 / 事件相关量 / 剔除量）"
                               "必须逐处写明各是什么（CONTRACT-joint §10.1）"
                % (plat, "、".join(sorted(vals))))
    
    # ---------- R4 证据不足须向语料反查 ----------
    if corpus:
        texts = [str(r.get("content", "")) for r in corpus]
        bad = []
        for seg in _split_claims(text)[0]:
            mchip = re.search(r'<span class="v[^"]*"[^>]*>([^<]*)</span>', seg)
            mtxt = re.search(r'<p class="claim-txt">(.*?)</p>', seg, re.S)
            mrea = re.search(r"<summary>说明与证据源</summary><p>(.*?)</p>", seg, re.S)
            if not (mchip and mtxt):
                continue
            if "证据不足" not in mchip.group(1):
                continue
            corpus_says = re.sub(r"<[^>]+>", "", (mrea.group(1) if mrea else ""))
            if not re.search(r"仅见|仅有一条|单条|单一|无交叉来源|无媒体报道", corpus_says):
                continue
            ctxt = re.sub(r"<[^>]+>", "", mtxt.group(1))
            for w in {c for c in re.findall(r"[\u4e00-\u9fa5]{3,6}", ctxt)}:
                hit = sum(1 for t in texts if w in t)
                if hit >= 5:
                    bad.append((ctxt[:26], w, hit))
                    break
        if bad:
            add("error", "R4", "以下断言以「仅见单条/单源」为由判「证据不足」，但分析语料中命中记录远多于 1 条："
                               "%s（裁决理由须与语料规模相符，或写明语料记录为何不构成证据，CONTRACT-joint §10.3）"
                % "；".join("「%s…」词「%s」命中 %d 条" % b for b in bad[:4]))

    # ---------- R5 裁决必须回写叙事（跨章一致性）----------
    # 裁决粒度是"整条断言"，叙事粒度是"整段文字"，两者不重合。若某条断言被判
    # 「证据不足 / 失实」，其具名数字（含单位的）却在第一、二、六章被当事实直陈，
    # 同一份报告就自相矛盾。实测：第三章判「77 元」证据不足，第二章时间线仍写「赔付 77 元」。
    # 带「网传 / 未经核实」等限定的引用不算违规（契约 §6 允许带限定地提及）。
    fpath = os.path.join(data_dir, "facts.json")
    facts = None
    if os.path.exists(fpath):
        try:
            with open(fpath, encoding="utf-8-sig") as f:
                facts = json.load(f)
        except ValueError:
            facts = None
    if isinstance(facts, dict) and facts.get("claims"):
        # 参与比对的章节：一（含摘要）、二（时间线）、五（观点辨析）、六（舆论）；
        # 跳过三（登记册本身）、四（裁决）、七（来源索引）、附录。按章节锚点切分。
        segs = []
        for cid, nxt in (("ch1", "ch2"), ("ch2", "ch3"), ("ch5", "ch6"), ("ch6", "ch7")):
            m = re.search(r'<a id="%s"[^>]*>(.*?)(?=<a id="%s")' % (cid, nxt), text, re.S)
            if m:
                segs.append((cid, re.sub(r"<[^>]+>", " ", m.group(1))))
        hedge = r"网传|未经核实|未核实|尚未证实|传闻|据传|一说是|疑似|有说法|据称|缺乏可核出处"
        offenders = []
        for c in facts["claims"]:
            if not isinstance(c, dict):
                continue
            if str(c.get("verdict") or "") not in ("证据不足", "失实"):
                continue
            for tok in set(re.findall(r"(\d{2,})\s*(元|万元|万|条|次|名|岁|天|秒|分钟)", str(c.get("claim") or ""))):
                num, unit = tok
                pat = re.compile(re.escape(num) + r"\s*" + re.escape(unit))
                for cid, body_txt in segs:
                    for mm in pat.finditer(body_txt):
                        ctx = body_txt[max(0, mm.start() - 30):mm.end() + 30]
                        if not re.search(hedge, ctx) and not re.search(r"#\d{1,3}", ctx):
                            offenders.append("%s「%s%s」出现在%s章（%s）"
                                             % (str(c.get("id") or "?"), num, unit,
                                                {"ch1": "一", "ch2": "二", "ch5": "五", "ch6": "六"}[cid],
                                                re.sub(r"\s+", " ", ctx).strip()[:32]))
                            break
        if offenders:
            add("error", "R5", "以下断言的裁决是「证据不足/失实」，其具名数字却在别章被当事实直陈：%s"
                               "（裁决必须回写叙事：带「网传/未经核实」限定，或删去该数字，"
                               "CONTRACT-joint §6）" % "；".join(offenders[:4]))
    return issues


def check_text(text, fname="<string>"):
    """对整篇 HTML 文本跑规则；返回 {"errors": [...], "warnings": [...], "fname": fname}。"""
    issues = []
    lines = text.split("\n")

    def add(level, rid, lineno, msg):
        issues.append({"level": level, "id": rid, "line": lineno, "msg": msg,
                       "snippet": (lines[lineno - 1].strip()[:120] if 0 < lineno <= len(lines) else "")})

    body = _strip_tags_ish(text)

    # ---- E1 / E2 装配标记 ----
    starts = [i + 1 for i, ln in enumerate(lines) if "<!-- CONTENT_START -->" in ln]
    ends = [i + 1 for i, ln in enumerate(lines) if "<!-- CONTENT_END -->" in ln]
    if len(starts) != 1:
        add("error", "E1", starts[0] if starts else 0,
            "CONTENT_START 出现 %d 次（应恰好 1 次）：行 %s" % (len(starts), starts or "无"))
    if len(ends) != 1:
        add("error", "E2", ends[0] if ends else 0,
            "CONTENT_END 出现 %d 次（应恰好 1 次）：行 %s" % (len(ends), ends or "无"))
    elif starts and ends[0] < (starts[0] if starts else 0):
        add("error", "E2", ends[0], "CONTENT_END 出现在 CONTENT_START 之前")

    # ---- E3 md 管道表残留 ----
    for i, ln in enumerate(lines, 1):
        if re.search(r"^\s*<p>\s*\|", ln) or re.search(r"<p>\s*\|[^<]*\|", ln):
            add("error", "E3", i, "md 管道表格以 <p> 文本落下，需渲染为真 <table>")

    # ---- E4 空标题 ----
    for i, ln in enumerate(lines, 1):
        if re.search(r"<h[1-6]>\s*</h[1-6]>", ln):
            add("error", "E4", i, "空标题元素")

    # ---- E5 主体轨道网格列数不齐（v0.2.18）----
    for m in re.finditer(r'<table[^>]*class="[^"]*\brail\b[^"]*"[^>]*>(.*?)</table>', body, re.S):
        blk = m.group(1)
        mh = re.search(r"<thead\b.*?</thead>", blk, re.S)
        ncol = len(re.findall(r"<th\b", mh.group(0))) if mh else 0
        if not ncol:
            continue
        bad = None
        for rm in re.finditer(r"<tr[^>]*>(.*?)</tr>", blk, re.S):
            if "<td" not in rm.group(1):
                continue
            ntd = len(re.findall(r"<td\b", rm.group(1)))
            if ntd != ncol:
                bad = ntd
                break
        if bad is not None:
            add("error", "E5", 0, "主体轨道网格行列数不齐：表头 %d 列 / 数据行 %d 列（节点 col 越界或漏列，"
                                 "装配端应保证每行 td 数 = 阶段列数）" % (ncol, bad))

    # ---- W1 引用 ↔ 锚点 ----
    ids = set(REF_ID.findall(body))
    cites = set()
    for a, b in REF_CITE.findall(body):
        cites.add(a or b)
    missing = sorted(cites - ids)
    orphan = sorted(ids - cites)
    if missing:
        add("warning", "W1", 0, "正文引用了但索引无锚点：%s（前 8 个：%s）"
            % (len(missing), "、".join(missing[:8])))
    if orphan:
        add("warning", "W1", 0, "索引有锚点但正文未引用：%s（前 8 个：%s）"
            % (len(orphan), "、".join(orphan[:8])))

    # ---- W2 表格缺 thead ----
    n_tbl = len(TABLE_OPEN.findall(body))
    n_head = len(THEAD_OPEN.findall(body))
    if n_tbl > n_head:
        add("warning", "W2", 0, "%d 个表格中 %d 个缺 <thead>（斑马纹/打印表头不生效）"
            % (n_tbl, n_tbl - n_head))

    # ---- W3 ref 行缺来源分级 ----
    ref_rows = []
    for m in REF_ROW.finditer(body):
        tag = m.group(1)
        if not re.search(r'class="source-[ABC]"', tag):
            # 换算回行号（近似：在原文中找该行）
            ref_rows.append(m.group(0)[:60])
    if ref_rows:
        add("warning", "W3", 0, "%d 个来源锚点行缺 class=\"source-A/B/C\"（分级底色失效），示例：%s"
            % (len(ref_rows), " / ".join(ref_rows[:2])))

    # ---- W4 截断实体 ----
    # 注意：URL 查询串里的 &amp;xsec_source=… 是**正确转义**的 &，不是截断实体。
    # 早先的判据把这类 URL 全报成 warning（实测 18 条假警，把真警淹掉），
    # 故先剔除 href/src 属性里的 &amp;，再判"后面跟的是不是实体残余"。
    for i, ln in enumerate(lines, 1):
        probe = re.sub(r'(?:href|src)\s*=\s*"[^"]*"', '""', ln)
        probe = re.sub(r'(?:href|src)\s*=\s*\'[^\']*\'', "''", probe)
        if re.search(r"&amp;(?=[A-Za-z])", probe) or re.search(r"&[A-Za-z#][A-Za-z0-9#]*$", probe.strip()):
            add("warning", "W4", i, "疑似截断实体（&amp; 后紧跟字母 或 行尾半截实体）")

    # ---- W5 report-meta 页脚要素 ----
    if "theme-v:" not in text:   # 版本标记位于 HTML 注释内，须查原文
        add("warning", "W5", 0, "缺少 <!-- theme-v: x.y.z --> 版本标记")
    if not re.search(r'class="footer"|<footer\b', body):
        add("warning", "W5", 0, "缺少 .footer 页脚")
    if not re.search(r"生成(时间|器)|报告生成", body):
        add("warning", "W5", 0, "页脚缺少生成信息（生成器/生成时间）")

    # ---- W6 <p> 内 md 残留 ----
    for i, ln in enumerate(lines, 1):
        if re.search(r"<p>[^<]*\*\*", ln) or re.search(r"<p>[^<]*\[[^\]\n]*\]\(https?://", ln) \
                or re.search(r"<p>\s*#+\s", ln):
            add("warning", "W6", i, "<p> 内 markdown 残留（** / [t](url) / # 标题）")

    # ---- W7 相邻同级别标题 ----
    prev_tag, prev_line = None, None
    for i, ln in enumerate(lines, 1):
        m = re.match(r"^\s*<h([1-6])[^>]*>", ln)
        if not m:
            continue
        tag = m.group(1)
        if prev_tag == tag and i - prev_line <= 1:
            add("warning", "W7", i, "连续同级别标题 <h%s>（疑似双标题残留，上一处行 %d）" % (tag, prev_line))
        prev_tag, prev_line = tag, i

    # ---- W8 doctype ----
    first = next((ln for ln in lines if ln.strip()), "")
    if not re.match(r"^\s*<!DOCTYPE\s+html", first, re.I):
        add("warning", "W8", 1, "文件不是以 <!DOCTYPE html> 开头")

    # ---- W9 断言卡缺 short_title 标题 ----
    n_claim = body.count('class="claim"')
    n_title = body.count('class="claim-title"')
    if n_claim and n_title < n_claim:
        add("warning", "W9", 0, "%d 张断言卡中 %d 张缺 .claim-title（建议补 facts.json claims[].short_title）"
            % (n_claim, n_claim - n_title))

    # ---- W10 第一章缺事件摘要 ----
    if 'class="kpis"' in body and 'class="abstract"' not in body:
        add("warning", "W10", 0, "第一章有仪表盘但缺 .abstract 事件摘要（--abstract 注入）")

    # ---- W11 附录跳级（首个标题为 h4 → 自动编号 x.0.y）----
    m_app = re.search(r"<h2[^>]*>附录[^<]*</h2>", body)
    if m_app:
        tail = body[m_app.end():]
        nxt = re.search(r"<h2[^>]*>", tail)
        seg = tail[:nxt.start()] if nxt else tail
        m_sub = re.search(r"<h([3-6])[^>]*>", seg)
        if m_sub and m_sub.group(1) == "4":
            add("warning", "W11", 0, "附录下首个标题为 h4（缺 h3，自动编号会显示 x.0.y），建议补/降为 h3")

    # ---- W12–W14 主体轨道网格结构完整性（v0.2.18）----
    if 'class="rail-card"' in body:
        for rm in re.finditer(r'<tr class="main"[^>]*>(.*?)</tr>', body, re.S):
            if not re.search(r'class="rn\b', rm.group(1)):
                add("warning", "W12", 0, "主体轨道网格：主轴行（tr.main）无节点胶囊（空壳主轴——"
                                         "应补 nodes 或降为 layer=minor）")
        ids_card = set(x.strip() for x in re.findall(r'class="racard-id">([^<]+)<', body))
        ids_row = set(x.strip() for x in re.findall(r'class="aid">([^<]+)<', body))
        if ids_card != ids_row:
            add("warning", "W13", 0, "主体轨道网格：身份卡编号(.racard-id)与轨道行编号(.aid)不一致"
                                     "（卡独有 %s / 行独有 %s）"
                % ("、".join(sorted(ids_card - ids_row)) or "无",
                   "、".join(sorted(ids_row - ids_card)) or "无"))
        nums = [int(x) for seg in re.findall(r'<p class="racard-claims">(.*?)</p>', body, re.S)
                for x in re.findall(r"#(\d+)", seg)]
        if nums and n_claim and max(nums) > n_claim:
            add("warning", "W14", 0, "主体轨道网格：claims 引用 #%d 超出第三章断言数 %d（悬空引用，"
                                     "须与 facts.json 登记册对齐）" % (max(nums), n_claim))

    # ---- W15 立场卡组书写限制（.ssteps 破版条件：横向弹性槽 + 等高网格）----
    for sm in re.finditer(r'<div class="sstep">(.*?)</div>', body, re.S):
        plain = re.sub(r"\[\d+\]", "", re.sub(r"<[^>]+>", "", sm.group(1))).strip()
        if len(plain) > 14:
            add("warning", "W15", 0, "立场卡组单步 %d 字超限（≤14 字，超长会换行增高并把全部卡片"
                                     "拉伸到最高卡等高）：%s…" % (len(plain), plain[:20]))
    cards = re.findall(r'<article class="scard">(.*?)</article>', body, re.S)
    for cd in cards:
        n_step = len(re.findall(r'<div class="sstep">', cd))
        if n_step > 5:
            add("warning", "W15", 0, "立场卡组步骤数 %d 步超限（3–5 步）：%s…"
                % (n_step, re.sub(r"<[^>]+>", "", cd)[:20]))
        if n_step and re.search(r'class="quote-(?:txt|item)"', cd):
            add("warning", "W15", 0, "立场卡组卡内出现引语（引语卡反模式：与第六章「高赞代表」表重复，"
                                     "代表性原文应只放该表）：%s…" % re.sub(r"<[^>]+>", "", cd)[:20])

    # ---- W15 立场卡组书写限制（.ssteps 是横向弹性槽 + .scards 等高网格 → 超长即破版）----
    for sm in re.finditer(r'<div class="sstep">(.*?)</div>', body, re.S):
        plain = re.sub(r"\[\d+\]", "", re.sub(r"<[^>]+>", "", sm.group(1))).strip()
        if len(plain) > 14:
            add("warning", "W15", 0, "立场卡组单步 %d 字超限（≤14 字，超长会换行增高并把全部卡片"
                                     "拉伸到最高卡等高）：%s…" % (len(plain), plain[:20]))
    for cd in re.findall(r'<article class="scard">(.*?)</article>', body, re.S):
        n_step = len(re.findall(r'<div class="sstep">', cd))
        if n_step > 5:
            add("warning", "W15", 0, "立场卡组步骤数 %d 步超限（3–5 步）：%s…"
                % (n_step, re.sub(r"<[^>]+>", "", cd)[:20]))
        if n_step and re.search(r'class="quote-(?:txt|item)"', cd):
            add("warning", "W15", 0, "立场卡组卡内出现引语（引语卡反模式：与第六章「高赞代表」表重复，"
                                     "代表性原文应只放该表）：%s…" % re.sub(r"<[^>]+>", "", cd)[:20])
    for nt in re.findall(r'<p class="scard-note">(.*?)</p>', body, re.S):
        plain = re.sub(r"\[\d+\]", "", re.sub(r"<[^>]+>", "", nt)).strip()
        if len(plain) > 60:
            add("warning", "W15", 0, "立场卡组防呆行 %d 字超限（约 ≤55 字，各卡长度相近才不"
                                     "跨卡留白）：%s…" % (len(plain), plain[:20]))
    for vw in re.findall(r'<span class="scard-view">(.*?)</span>', body, re.S):
        plain = re.sub(r"<[^>]+>", "", vw).strip()
        if len(plain) > 20:
            add("warning", "W15", 0, "立场卡组视角行 %d 字超限（≤20 字，卡头为两行固定高度）：%s…"
                % (len(plain), plain[:20]))

    # ---- W16 第六章「高赞代表」引语应带引用编号（可回溯）----
    m5b = re.search(r"<h2[^>]*>[^<]*舆论观点综合[^<]*</h2>(.*?)(?=<h2)", body, re.S)
    if m5b:
        items = re.findall(r'<div class="quote-item">(.*?)</div>', m5b.group(1), re.S)
        if items:
            n_ref = sum(1 for x in items if re.search(r'class="ref"', x))
            if n_ref < len(items):
                add("warning", "W16", 0, "第六章「高赞代表」引语 %d/%d 条缺引用编号"
                                         "（样本应带 ref_url 并渲染为 [N]）" % (len(items) - n_ref, len(items)))

    errors = [x for x in issues if x["level"] == "error"]
    warnings = [x for x in issues if x["level"] == "warning"]
    return {"fname": fname, "errors": errors, "warnings": warnings,
            "n_error": len(errors), "n_warning": len(warnings)}


def _print_report(rep, verbose=True):
    if rep["n_error"] == 0 and rep["n_warning"] == 0:
        if verbose:
            print("  OK  （%s）" % rep["fname"])
        return True
    print("== %s  errors=%d warnings=%d ==" % (rep["fname"], rep["n_error"], rep["n_warning"]))
    for it in rep["errors"] + rep["warnings"]:
        loc = ("行 %d" % it["line"]) if it["line"] else "—"
        print("  [%s] %-2s  %s: %s" % (it["id"], it["level"][0].upper(), loc, it["msg"]))
        if it["snippet"]:
            print("        … %s" % it["snippet"])
    return rep["n_error"] == 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="HTML 报告静态自检器（CONTRACT.md §7）")
    ap.add_argument("files", nargs="+", help="待检查的 .html 文件")
    ap.add_argument("--strict", action="store_true", help="warning 也计为失败")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON 汇总")
    ap.add_argument("--quiet", action="store_true", help="通过时不打印 OK")
    ap.add_argument("--joint", action="store_true", help="联合任务模式：追加 G1–G8 检查与 GW 人工复核清单（CONTRACT-joint）")
    ap.add_argument("--data", default="", help="data/{event_id} 目录：启用数据对账 R1–R4（CONTRACT-joint §10），须与 --joint 同用")
    ap.add_argument("--allow", default="", help="豁免指定检查 id（逗号分隔，如 --allow G6；须在方法论记录理由）")
    args = ap.parse_args(argv)

    results = []
    overall = True
    for p in args.files:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            print("[E] 无法读取 %s：%s" % (p, e))
            overall = False
            continue
        rep = check_text(text, fname=p)
        if args.joint:
            joint_issues = check_joint(text, fname=p)
            if args.data:
                joint_issues.extend(check_joint_data(text, args.data, fname=p))
                joint_issues.extend(check_artifacts(args.data))
            rep["errors"].extend(x for x in joint_issues if x["level"] == "error")
            rep["warnings"].extend(x for x in joint_issues if x["level"] == "warning")
            rep["warnings"].extend({"level": "warning", "id": "GW", "line": 0, "msg": t, "snippet": ""}
                                   for t in _JOINT_MANUAL)
            rep["n_error"] = len(rep["errors"])
            rep["n_warning"] = len(rep["warnings"])
        if args.allow:
            allow = {x.strip() for x in args.allow.split(",") if x.strip()}
            rep["errors"] = [x for x in rep["errors"] if x["id"] not in allow]
            rep["warnings"] = [x for x in rep["warnings"] if x["id"] not in allow]
            rep["n_error"] = len(rep["errors"])
            rep["n_warning"] = len(rep["warnings"])
        ok = _print_report(rep, verbose=not args.quiet)
        if args.strict:
            ok = ok and rep["n_warning"] == 0
        overall = overall and ok
        results.append(rep)

    if args.json:
        print(json.dumps({"overall": "ok" if overall else "fail", "files": results},
                         ensure_ascii=False, indent=1))
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
