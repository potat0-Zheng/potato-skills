# -*- coding: utf-8 -*-
"""HTML 报告静态自检器（report-theme / CONTRACT.md §7）。

用法：
    python check_report.py [--strict] [--json] [--quiet] 文件.html [文件2.html …]

规则（详见 CONTRACT.md §7）：
  E1  CONTENT_START 必须恰好 1 次
  E2  CONTENT_END 必须恰好 1 次且位于 START 之后
  E3  <p> 内不得出现 md 管道表格残留（<p>|）
  E4  标题元素不得为空
  W1  正文 .ref 引用 ↔ id="refN" 锚点应双向一致
  W2  表格应带 <thead>（斑马纹/打印表头依赖）
  W3  tr[id^="ref"] 应带 class="source-A/B/C"
  W4  疑似截断实体（&amp; 后紧跟字母 / 行尾半截实体）
  W5  应含 report-meta 页脚要素（theme-v 标记 / .footer / 生成信息）
  W6  <p> 内 md 残留（** / [t](url) / # 标题语法）
  W7  相邻同级别标题（疑似双标题残留）
  W8  文件应以 <!DOCTYPE html> 开头
  W9  断言卡缺 .claim-title（short_title）
  W10 第一章缺 .abstract 事件摘要
  W11 附录下首个标题为 h4（跳级 x.0.y）

联合模式（--joint，CONTRACT-joint）：
  G1  合规账本缺失/字段不全/跳过无用户确认
  G2  缺联合 meta 标记
  G3  裁决 chip 混入 ▲●△
  G4  裁决「真实」但证据等级 ≤ 转载层
  G5  否定性主张判「真实」
  G6  事件进行中仍出现确定性走势表述
  GW  人工复核清单（静态提示，warning 级）
  --allow <id,…>  显式豁免指定检查（如 --allow G6，须在方法论记录理由）

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
    for seg in re.split(r'(?=<article class="claim">)', text):
        if 'class="claim"' not in seg:
            continue
        mchip = re.search(r'<span class="v[^"]*"[^>]*>([^<]*)</span>', seg)
        chip = mchip.group(1) if mchip else ""
        mtxt = re.search(r'<p class="claim-txt">(.*?)</p>', seg, re.S)
        ctxt = re.sub(r"<[^>]+>", "", mtxt.group(1)) if mtxt else ""
        mev = re.search(r"证据等级[:：]\s*([^<\n]{0,24})", seg)
        evl = mev.group(1) if mev else ""
        if chip == "真实" and any(k in evl for k in ("转载", "摘要", "标题层")):
            add("error", "G4", "断言「%s…」chip=真实 但证据等级=「%s」≤转载层（应显示「报道属实（原文未核）」）"
                % (ctxt[:24], evl))
        if chip == "真实" and re.search(r"未见|未回应|未表态|未答复|未直接回应|并未回应|没有回应|尚未回应"
                                        r"|未就[^，。；]{0,15}?(?:回应|表态|答复|发声)", ctxt):
            add("error", "G5", "否定性主张「%s…」裁决为「真实」（应默认「证据不足」，除非权威否定声明）" % ctxt[:32])
    if re.search(r"进行中|仍在(发酵|持续)|尚未平息", text):
        m5 = re.search(r"<h2[^>]*>五、舆论观点综合</h2>(.*?)(?=<h2[^>]*>六、)", text, re.S)
        trend = m5.group(1) if m5 else text
        mdet = re.search(r"回落|趋于平息|即将平息|热度消退|已(?:经)?结束|尘埃落定", trend)
        if mdet:
            add("error", "G6", "事件状态=进行中 但舆论章出现确定性走势表述「%s」（应改情景句，见 CONTRACT-joint §5；"
                               "确有异议用 --allow G6 并记录理由）" % mdet.group(0))
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
    for i, ln in enumerate(lines, 1):
        if re.search(r"&amp;(?=[A-Za-z])", ln) or re.search(r"&[A-Za-z#][A-Za-z0-9#]*$", ln.strip()):
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
    ap.add_argument("--joint", action="store_true", help="联合任务模式：追加 G1–G6 检查与 GW 人工复核清单（CONTRACT-joint）")
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
            rep["errors"].extend(x for x in check_joint(text, fname=p) if x["level"] == "error")
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
