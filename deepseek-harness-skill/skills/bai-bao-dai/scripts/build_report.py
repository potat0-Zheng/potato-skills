# -*- coding: utf-8 -*-
"""bai-bao-dai 综合报告生成（9 章结构）。

读 facts.json / opinion.json / normalized jsonl / sources.json，
用 templates/report_template.html 渲染 report.html。章节：
  一、结论速览      二、事件时间线      三、核心事实汇编
  四、事实核查结果  五、观点辨析与推理说明  六、舆论观点综合
  七、来源索引        八、覆盖完整性声明    九、方法论          十、信息与生成时间戳

LLM 深度内容（视点综合 / 覆盖完整性补充 / 事实还原正文）通过
--viewpoint / --coverage / --extra 三个 markdown 文件注入并渲染为 HTML。

报告自动生成章节目录（<nav class="toc"> + ch1..ch9 章节锚点），便于长文跳转。
可用 --template <path> 指定模板文件（默认读技能自带副本；供"副本先行"验证用）。

用法：
  python build_report.py --event "事件关键词" \
      --facts data/demo/facts.json --opinion data/demo/opinion.json \
      --normalized data/demo/normalized/data.jsonl \
      --sources data/demo/sources.json --viewpoint data/demo/viewpoint.md \
      --out data/demo/report.html
"""
import argparse
import json
import os
import re
from datetime import date, datetime

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(SKILL_DIR, "templates", "report_template.html")

PLATFORM_NAMES = {"zhihu": "知乎", "weibo": "微博", "xiaohongshu": "小红书", "unknown": "未知"}
VERDICT_ORDER = ["真实", "部分真实", "失实", "证据不足", "待核查"]
# v0.2.13 图表色环：与模板 .stack-seg.s1-.s6 / .trend .s1-.s4 / .donut 分段一致
S_PALETTE = ["#b0573b", "#8a6512", "#66794f", "#3d3d3a", "#d1cfc5", "#87786a"]

EV_LABELS = {1: "官方文件/本人账号原文", 2: "权威媒体全文", 3: "转载引文", 4: "搜索摘要/标题层"}

# 章节稳定 id（v0.5）：--intros 一律按 id 取。
# 此前按章节下标（"5"）取，插一章就会把"立场占比"的导读搬到别的章上——静默错位。
CH_IDS = ["overview", "timeline", "facts", "check", "analysis",
          "opinion", "sources", "coverage", "method", "timestamp"]
# 旧文件的数字键 → 新 id（向后兼容；不映射就等于静默丢弃用户写的导读）
LEGACY_INTRO_IDX = {"1": "overview", "2": "timeline", "3": "facts", "4": "check",
                    "5": "opinion", "6": "sources", "7": "coverage", "8": "method",
                    "9": "timestamp"}


def _ev_label(v):
    """断言证据等级标签：facts.json claims[].evidence_level（1-4 或中文标签，见 CONTRACT-joint §2）。"""
    if v in (None, ""):
        return ""
    s = str(v).strip()
    if s.isdigit() and int(s) in EV_LABELS:
        return EV_LABELS[int(s)]
    return s


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def load_json(path):
    if not path or not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _inline(text):
    """行内 markdown：加粗/斜体/链接/行内代码 + 引用编号 [N] → a.ref（v0.2.13）。
    text 需已 esc()；标签内文本用哨兵保护，避免对 <a>/<code> 内容二次转换。"""
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    # 引用编号 → 交叉引用（悬停 refpop 卡片）：保护 <a>/<code>/<strong>/<em> 内文本
    holders = []

    def _hold(m):
        holders.append(m.group(0))
        return "\x00REFHOLD%d\x00" % (len(holders) - 1)

    text = re.sub(r"<a\b[^>]*>.*?</a>", _hold, text, flags=re.S)
    text = re.sub(r"<code>.*?</code>", _hold, text, flags=re.S)
    text = re.sub(r"<strong>.*?</strong>", _hold, text, flags=re.S)
    text = re.sub(r"<em>.*?</em>", _hold, text, flags=re.S)
    text = re.sub(r"\[(\d{1,3})\]", r'<a href="#ref\1" class="ref">[\1]</a>', text)
    for i, h in enumerate(holders):
        text = text.replace("\x00REFHOLD%d\x00" % i, h)
    return text


def _strip_head_prefix(text):
    """剥离小节标题手写序号前缀（v0.2.8 自动编号会叠号）：中文「一、二、…」或「1. / 1、/ 1)」。"""
    t = (text or "").strip()
    t = re.sub(r"^[一二三四五六七八九十]+、", "", t)
    t = re.sub(r"^\d+\s*[.、．)）]\s*", "", t)
    return t


def _verdict_class(verdict):
    """裁决徽章配色（CONTRACT-joint §3 四档）：真实 v-true / 部分真实·观点 v-part /
    失实 v-false / 证据不足·待核查 v-none。未知值兜底 v-none。"""
    v = str(verdict or "")
    if "观点" in v:
        return "v-part"
    if "部分" in v:
        return "v-part"
    if "失实" in v:
        return "v-false"
    if "证据不足" in v or v in ("待核查", "无法核实", "无证据", "存疑"):
        return "v-none"
    return "v-true"


def _clip_text(s, n=56):
    """清洗语料文本后截断：剥行内 markdown 标记、折行压空格，超长补省略号。"""
    t = str(s or "")
    t = re.sub(r"\*\*|__|`|^#{1,6}\s*", "", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def _abstract_html(text):
    """事件摘要（v0.2.16）：自动识别两种 md 写法——
    结构化标签式：每个标签单独成段写 `**一句话结论**` 等，其后的段落为该标签内容，
      渲染为 .abstract dl（dt 标签 + dd 内容，见模板注释的推荐标签序）；
    旧两段式：无标签段落（空行分段），渲染为 .abs-label + .abs-txt 段落（兼容保留）。"""
    t = (text or "").strip()
    if not t:
        return ""
    paras = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    if not paras:
        return ""
    LABEL = re.compile(r"^\*\*(.+?)\*\*$")
    # 结构化模式：段落恰好是 **标签** 视为 dt，其后段落为该标签内容
    if any(LABEL.match(p) for p in paras):
        inner = ["<dl>"]
        cur_label = None
        cur_body = []
        def flush():
            nonlocal cur_label, cur_body
            if cur_label is not None:
                body = "".join("<p>%s</p>" % _inline(esc(b)) for b in cur_body) if cur_body else ""
                inner.append("<dt>%s</dt><dd>%s</dd>" % (esc(cur_label), body))
            cur_label, cur_body = None, []
        for p in paras:
            m = LABEL.match(p)
            if m:
                flush()
                cur_label = m.group(1).strip()
            else:
                cur_body.append(p)
        flush()
        inner.append("</dl>")
        inner.append('<p class="abs-foot">本摘要由模型生成，仅作全文压缩；细节与结论以各章数据与来源为准。</p>')
        return '<div class="abstract">' + "".join(inner) + "</div>"
    # 旧两段式（兼容）
    inner = ['<span class="abs-label">摘要</span>']
    for p in paras:
        inner.append('<p class="abs-txt">%s</p>' % _inline(esc(p)))
    inner.append('<p class="abs-txt" style="margin-top:.4em;color:var(--gray-500);'
                 'font-family:var(--mono);font-size:11.5px;letter-spacing:.02em">'
                 '本摘要由模型生成，仅作全文压缩；细节与结论以各章数据与来源为准。</p>')
    return '<div class="abstract">' + "".join(inner) + "</div>"


def _bnav_html():
    """左侧常驻目录栏（v0.2.12 皮肤已含样式）：骨架 + 内联 JS（仅滚动高亮当前章）。"""
    return r"""<aside class="bnav" id="bnavPanel" aria-label="章节目录">
  <div class="bnav-head"><span class="t">目录</span></div>
  <ul class="bnav-list" id="bnavList"></ul>
  <div class="bnav-foot"><span id="bnavCount"></span></div>
</aside>
<script>
(function () {
  var list = document.getElementById('bnavList'), secs = [];
  document.querySelectorAll('a.sec-anchor').forEach(function (a) {
    var h = a.nextElementSibling;
    if (!h || h.tagName !== 'H2') return;
    var id = a.id; if (!id) return;
    var txt = (h.textContent || '').replace(/\s+/g, ' ').trim(); if (!txt) return;
    secs.push({ id: id, text: txt });
  });
  secs.forEach(function (s) {
    var li = document.createElement('li'), link = document.createElement('a');
    link.className = 'bnav-item'; link.href = '#' + s.id; link.title = s.text;
    var span = document.createElement('span'); span.className = 't'; span.textContent = s.text;
    link.appendChild(span); li.appendChild(link); list.appendChild(li); s.link = link;
  });
  var count = document.getElementById('bnavCount'); if (count) count.textContent = secs.length + ' 章';
  function spy() {
    /* v0.2.13-fix：rect.top 是视口坐标，阈值必须同为视口坐标（此前误用 scrollY+偏移 → 末章恒胜出） */
    var mark = Math.max(96, innerHeight * 0.3), cur = secs.length ? secs[0] : null;
    secs.forEach(function (s) { var el = document.getElementById(s.id); if (el && el.getBoundingClientRect().top <= mark) cur = s; });
    secs.forEach(function (s) { s.link.classList.toggle('active', s === cur); });
  }
  window.addEventListener('scroll', spy, { passive: true });
  window.addEventListener('resize', spy);
  spy();
})();
</script>"""


def _fold_js():
    """折叠渐缓弹出（v0.2.13）：WAAPI 命令式动画，每次 open 均重播。与 _poc/_build_demo.py 内联 JS 同源。"""
    return r'''<script>
(function () {
  if (!document.documentElement || !('animate' in document.documentElement)) return;
  function popFold(d) {
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    var kids = d.children, i, j, c, anims;
    for (i = 0; i < kids.length; i++) {
      c = kids[i];
      if (c.tagName === 'SUMMARY') continue;
      try {
        if (c.getAnimations) { anims = c.getAnimations(); for (j = 0; j < anims.length; j++) anims[j].cancel(); }
      } catch (err) {}
      c.animate(
        [{ opacity: 0, transform: 'translateY(-6px)' },
         { opacity: 1, transform: 'translateY(0)' }],
        { duration: 300, easing: 'cubic-bezier(.22,.9,.24,1)', fill: 'both' }
      );
    }
  }
  document.addEventListener('click', function (e) {
    var t = e.target, s, d;
    if (!t || !t.closest) return;
    s = t.closest('summary');
    if (!s) return;
    d = s.parentElement;
    if (!d || !d.classList || !d.classList.contains('fold') || d.open) return;
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { popFold(d); });
    });
  });
})();
</script>'''


def _refpop_js():
    """引用悬停气泡卡（v0.2.13）：悬停/聚焦 a.ref（#refN）读取本页索引行浮出。与 _poc/_build_demo.py 内联 JS 同源。"""
    return r'''<script>
(function () {
  var pop = null, hideTimer = null;
  function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
  function txt(el) { return el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : ''; }
  function hide() {
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    if (pop) { pop.remove(); pop = null; }
  }
  function deferHide() { hideTimer = setTimeout(hide, 120); }
  function show(a) {
    var m = (a.getAttribute('href') || '').match(/^#(ref\d+)$/);
    if (!m) return;
    var row = document.getElementById(m[1]);
    if (!row || !row.cells || !row.cells.length) return;
    hide();
    var cells = row.cells, html = '', meta = [], i, t, link;
    html += '<span class="rp-num">' + esc(txt(cells[0])) + '</span> ';
    if (cells.length > 1) html += '<span class="rp-src">' + esc(txt(cells[1])) + '</span>';
    if (cells.length > 2) html += '<div class="rp-title">' + esc(txt(cells[2])) + '</div>';
    for (i = 3; i < cells.length; i++) { t = txt(cells[i]); if (t && t !== '-' && t !== '—') meta.push(t); }
    if (meta.length) html += '<div class="rp-meta">' + meta.map(esc).join(' · ') + '</div>';
    link = row.querySelector('a[href^="http"]');
    if (link) html += '<div class="rp-link"><a href="' + link.href + '" target="_blank" rel="noopener">查看原文 ↗</a></div>';
    pop = document.createElement('div');
    pop.className = 'refpop';
    pop.innerHTML = html;
    document.body.appendChild(pop);
    var r = a.getBoundingClientRect(), pw = pop.offsetWidth, ph = pop.offsetHeight;
    var x = r.left + window.scrollX, y = r.bottom + window.scrollY + 8;
    if (x + pw > window.scrollX + document.documentElement.clientWidth - 8) x = Math.max(8, x - pw + r.width);
    if (y + ph > window.scrollY + window.innerHeight - 8) y = Math.max(8, r.top + window.scrollY - ph - 8);
    pop.style.left = x + 'px'; pop.style.top = y + 'px';
    pop.addEventListener('mouseleave', deferHide);
  }
  document.addEventListener('mouseover', function (e) {
    var t = e.target; if (!t || !t.closest) return;
    var a = t.closest('a.ref');
    if (a && /#ref\d+$/.test(a.getAttribute('href') || '')) show(a);
  });
  document.addEventListener('mouseout', function (e) {
    var t = e.target; if (!t || !t.closest) return;
    if (t.closest('a.ref') || (pop && pop.contains(t))) return;
    deferHide();
  });
  document.addEventListener('focusin', function (e) {
    var t = e.target;
    if (t && t.classList && t.classList.contains('ref')) show(t);
  });
  document.addEventListener('focusout', function () { deferHide(); });
  window.addEventListener('scroll', hide, true);
  window.addEventListener('resize', hide);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') hide(); });
})();
</script>'''


def md_to_html(md):
    """极简 markdown → HTML（标题/表格/列表/引用/加粗/斜体/链接/段落）。

    v0.2.9+：支持 GFM 管道表格（表头 + --- 分隔行），渲染为
    <table><thead><tbody>，与 report_template.html 表格样式（含 tbody 斑马纹/打印表头）
    对齐；分隔行 :--- / :---: / ---: 映射 th/td 的 text-align。
    与 report-theme/md_render.py 同算法（技能内自包含副本）。
    """
    def _split_row(s):
        s = s.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        return [p.strip() for p in s.split("|")]

    def _table_from(lines, i):
        """从 lines[i]（以 | 开头的疑似表头行）解析管道表；返回 (html, 下一未消费行下标)。"""
        head = lines[i].strip()
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            return None, i
        sep = lines[j].strip()
        if not (re.match(r"^\|?[\s:|-]*:?-+:?[\s:|-]*\|?$", sep)
                and "-" in sep and "|" in sep):
            return None, i
        heads = _split_row(head)
        aligns = []
        for cell in _split_row(sep):
            c = cell.strip()
            if c.startswith(":") and c.endswith(":") and len(c) > 2:
                aligns.append("center")
            elif c.endswith(":"):
                aligns.append("right")
            elif c.startswith(":"):
                aligns.append("left")
            else:
                aligns.append("")
        ths = "".join(
            f'<th style="text-align:{aligns[k]}">{_inline(esc(heads[k] if k < len(heads) else ""))}</th>'
            if k < len(aligns) and aligns[k] else
            f"<th>{_inline(esc(heads[k] if k < len(heads) else ''))}</th>"
            for k in range(len(heads))
        )
        rows = []
        k = j + 1
        while k < len(lines) and lines[k].strip().startswith("|"):
            parts = _split_row(lines[k])
            tds = "".join(
                f'<td style="text-align:{aligns[c]}">{_inline(esc(parts[c] if c < len(parts) else ""))}</td>'
                if c < len(aligns) and aligns[c] else
                f"<td>{_inline(esc(parts[c] if c < len(parts) else ''))}</td>"
                for c in range(len(heads))
            )
            rows.append(f"<tr>{tds}</tr>")
            k += 1
        html = f"<table>\n<thead>\n<tr>{ths}</tr>\n</thead>\n<tbody>\n" + "\n".join(rows) + "\n</tbody>\n</table>"
        return html, k

    def _flush(out, in_list, in_quote):
        if in_list:
            out.append("</ul>")
        if in_quote:
            out.append("</blockquote>")
        return False, False

    lines = md.split("\n")
    out, in_list, in_quote = [], False, False
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            in_list, in_quote = _flush(out, in_list, in_quote)
            i += 1
            continue
        # 管道表优先于其他块类型判定
        if line.strip().startswith("|"):
            tbl, nxt = _table_from(lines, i)
            if tbl is not None:
                in_list, in_quote = _flush(out, in_list, in_quote)
                out.append(tbl)
                i = nxt
                continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            in_list, in_quote = _flush(out, in_list, in_quote)
            lvl = len(m.group(1))
            out.append(f"<h{lvl + 2}>{_inline(esc(_strip_head_prefix(m.group(2))))}</h{lvl + 2}>")
            i += 1
            continue
        if re.match(r"^\s*[-*]\s+", line):
            if in_quote:  # 新块先闭合上一容器，避免列表嵌进 <blockquote>
                out.append("</blockquote>"); in_quote = False
            if not in_list:
                out.append("<ul>"); in_list = True
            item = re.sub(r"^\s*[-*]\s+", "", line)
            out.append(f"<li>{_inline(esc(item))}</li>")
            i += 1
            continue
        if re.match(r"^\s*>\s?", line):
            if in_list:  # 新块先闭合上一容器，避免 <blockquote> 嵌进 <ul>
                out.append("</ul>"); in_list = False
            if not in_quote:
                out.append("<blockquote>"); in_quote = True
            out.append(_inline(esc(re.sub(r"^\s*>\s?", "", line))))
            i += 1
            continue
        in_list, in_quote = _flush(out, in_list, in_quote)
        out.append(f"<p>{_inline(esc(line))}</p>")
        i += 1
    in_list, in_quote = _flush(out, in_list, in_quote)
    return "\n".join(out)


def load_normalized(path):
    recs = []
    if not path or not os.path.exists(path):
        return recs
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    recs.append(json.loads(line))
    except Exception as e:
        print(f"[report] 警告：normalized 读取失败：{e}")
    return recs


# ---------- 章节生成 ----------
def _span_from_milestones(milestones, year_hint=""):
    """从时间线里程碑取事件跨度（最早/最晚节点）。MM-DD 用 year_hint 补齐年份。

    事件跨度必须由事件时间线决定，不能用语料 min/max（v0.4 修 KPI「851 天」缺陷）。
    """
    days = []
    for m in milestones or []:
        if not isinstance(m, dict):
            continue
        d = str(m.get("date", "")).strip()
        for tok in re.findall(r"\d{4}-\d{2}-\d{2}|\d{2}-\d{2}", d):
            if len(tok) == 10:
                days.append(tok)
            elif year_hint:
                days.append("%s-%s" % (year_hint, tok))
    return (min(days), max(days)) if days else ("", "")


def _event_span(recs, data_meta=None):
    """事件窗口与统计口径的唯一来源。

    返回 (start, end, raw_total, relevant_total, dropped)：
      start/end   事件跨度（YYYY-MM-DD）——优先取时间线节点（data_meta 传入），
                  否则取语料中"出现次数最多的月份"内的最早/最晚日（排除背景/历史坐标）
      raw_total   治理前的语料量（data.jsonl），用于「原始 N 条 → 可用 M 条」标注
      relevant    进入分析的语料量
      dropped     raw_total - relevant（相关度过滤剔除量）
    """
    dm = data_meta or {}
    raw_total = int(dm.get("raw_total") or 0)
    relevant = int(dm.get("relevant_total") or (len(recs) if recs else 0))
    dropped = dm.get("dropped") or 0
    start, end = dm.get("start") or "", dm.get("end") or ""
    if not (start and end):
        # 回退：取"事件月"（记录最多的月份）的当日区间，排除背景/历史坐标
        months = {}
        for r in (recs or []):
            t = str(r.get("time", ""))[:10]
            if len(t) == 10 and t[4] == "-":
                months[t[:7]] = months.get(t[:7], 0) + 1
        if months:
            main_month = max(months.items(), key=lambda kv: kv[1])[0]
            days = sorted(str(r.get("time", ""))[:10] for r in (recs or [])
                          if str(r.get("time", ""))[:7] == main_month)
            if days:
                start, end = days[0], days[-1]
    return start, end, raw_total or relevant, relevant, dropped


def _kpi(num_html, lab, sub):
    """仪表盘 KPI 卡：num_html 为大数字（可含 <span> 单位），lab 标签，sub 说明。"""
    return ('<div class="kpi-card"><div class="kpi-num">%s</div>'
            '<div class="kpi-lab">%s</div><div class="kpi-sub">%s</div></div>'
            % (num_html, esc(lab), esc(sub) if sub else ""))


def sec_overview(event, facts, opinion, recs, abstract_text="", data_meta=None):
    c = ["<h2>一、结论速览</h2>"]
    _abs = _abstract_html(abstract_text)
    if _abs:
        c.append(_abs)
    c.append('<div class="kpis" aria-label="报告仪表盘">')
    start, end, raw_total, total, dropped = _event_span(recs, data_meta)
    plat = _platform_counts(recs)
    sub1 = "、".join("%s %d" % (PLATFORM_NAMES.get(p, p), n) for p, n in plat.items()) or "无采集记录"
    if dropped:
        sub1 += "；原始采集 %d 条，相关度过滤剔除 %d 条" % (raw_total, dropped)
    c.append(_kpi("%d<span>条</span>" % total, "分析语料", sub1))
    if facts and facts.get("claims"):
        cnt = {v: 0 for v in VERDICT_ORDER}
        for cl in facts["claims"]:
            v = cl.get("verdict", "待核查")
            cnt[v] = cnt.get(v, 0) + 1
        sub2 = "；".join("%s %d" % (v, n) for v, n in cnt.items() if n) or "待核查"
        c.append(_kpi("%d<span>条</span>" % len(facts["claims"]), "事实断言", sub2))
    else:
        c.append(_kpi("—", "事实断言", "未提供核查数据（facts.json）"))
    if opinion and opinion.get("stance_distribution"):
        dist = opinion["stance_distribution"]
        top = dist[0]
        cov = (opinion or {}).get("coverage_rate")
        sub3 = "主立场：%s（%.1f%%）" % (top["stance"], top["pct"])
        if cov:
            sub3 += "；归类覆盖率 %.1f%%（占比仅限可识别立场内部）" % float(cov)
        minority = [d["stance"] for d in dist if d.get("minority")]
        if minority:
            sub3 += "；少数派：" + "、".join(minority)
        c.append(_kpi("%d<span>个</span>" % len(dist), "舆论立场", sub3))
    else:
        c.append(_kpi("—", "舆论立场", "未提供舆论数据（opinion.json）"))
    if start and end and start != end:
        span = "%s ~ %s" % (start, end)
        try:
            days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
            c.append(_kpi("%d<span>天</span>" % days, "事件跨度", span))
        except Exception:
            c.append(_kpi("—", "事件跨度", span))
    elif start:
        c.append(_kpi("1<span>天</span>", "事件跨度", start))
    else:
        c.append(_kpi("—", "事件跨度", "无时间数据"))
    c.append("</div>")
    c.append('<p class="legend">确定性标记：▲官方确认 / ●多源一致 / △单源存疑。数值由脚本自动汇总，深度结论见后续章节。</p>')
    return "\n".join(c)


def _platform_counts(recs):
    out = {}
    for r in recs:
        out[r.get("platform", "unknown")] = out.get(r.get("platform", "unknown"), 0) + 1
    return out


def _default_intros(facts, opinion, sources, recs):
    """程序化默认章节导读（导航句）。键＝章节稳定 id（见 CH_IDS），不用下标。
    无数据的章节不生成（章内自带空提示）；overview 以仪表盘为导读，不生成。"""
    out = {}
    tl = []
    if opinion and opinion.get("timeline"):
        tl = sorted(opinion["timeline"].keys())
    elif recs:
        tl = sorted({str(r.get("time", ""))[:10] for r in recs if r.get("time")})
    if len(tl) >= 2:
        out["timeline"] = "时间线共 %d 个记录日（%s ~ %s），按日聚合采集记录数。" % (len(tl), tl[0], tl[-1])
    elif tl:
        out["timeline"] = "时间线记录日：%s（本事件仅 1 个记录日）。" % tl[0]
    if facts and facts.get("claims"):
        n = len(facts["claims"])
        out["facts"] = "本章汇编 %d 条事实断言及其原始表述/出处；逐条裁决见第四章。" % n
        cnt = {v: 0 for v in VERDICT_ORDER}
        for cl in facts["claims"]:
            v = cl.get("verdict", "待核查")
            cnt[v] = cnt.get(v, 0) + 1
        s = "；".join("%s %d" % (v, c) for v, c in cnt.items() if c)
        out["check"] = "LLM 已对 %d 条断言裁决：%s；证据等级与裁决词见各章标注。" % (n, s)
    if (facts or {}).get("opinion_analysis"):
        out["analysis"] = "对 %d 组主张作观点辨析：隐含前提 / 逻辑谬误 / 立场偏差与推导链。" % \
            len(facts["opinion_analysis"])
    if opinion and opinion.get("stance_distribution"):
        dist = opinion["stance_distribution"]
        top = dist[0]
        minority = [d["stance"] for d in dist if d.get("minority")]
        m = "；少数派：" + "、".join(minority) if minority else ""
        out["opinion"] = "舆论按 %d 个立场聚类：主立场「%s」（%.1f%%）%s；视点综合见本章后半。" % (len(dist), top["stance"], top["pct"], m)
    srcs = _collect_sources(sources, recs, _cited_sources(opinion))
    if srcs:
        n_cur = len(sources or []) + len(_cited_sources(opinion))
        extra = len(srcs) - n_cur
        if extra > 0:
            out["sources"] = "来源索引共 %d 条：前 %d 条为精选信源，其后 %d 条为平台语料样本链接（供逐帖回溯；转载簇去重见第八章）；正文证据以第三章「证据源」列表呈现。A=官方/权威，B=主流，C=自媒体/样本。" % (len(srcs), n_cur, extra)
        else:
            out["sources"] = "来源索引共 %d 条；A=官方/权威，B=主流，C=自媒体。" % len(srcs)
    plat = _platform_counts(recs)
    plat_s = "、".join("%s %d" % (PLATFORM_NAMES.get(p, p), n) for p, n in plat.items()) or "无采集"
    out["coverage"] = "平台覆盖：%s；未覆盖平台/未回应方与信源缺口见本章表格与补充说明。" % plat_s
    out["method"] = "本章说明报告生成方法（采集→事实还原→舆论把握→综合）与确定性标记规则。"
    out["timestamp"] = "本章记录报告生成时刻与信息截止时间；搜索索引存在延迟，结论可能滞后。"
    return out


def _stance_donut(dist):
    """环形图 .donut + .donut-legend（v0.2.13）：由 stance_distribution 各立场占比生成。
    返回 (html, ok)——数据不足 2 段时 ok=False，装配层回退纯条形布局。"""
    rows = []
    for d in dist or []:
        pct = float(d.get("pct") or 0)
        if pct > 0:
            rows.append((str(d.get("stance", "")), pct, int(d.get("count") or 0)))
    if len(rows) < 2:
        return "", False
    total = sum(p for _, p, _ in rows) or 1.0
    parts, cum = [], 0.0
    for i, (label, p, _) in enumerate(rows):
        frac = p / total * 100.0
        end = cum + frac if i < len(rows) - 1 else 100.0
        parts.append("%s %.4f%% %.4f%%" % (S_PALETTE[i % len(S_PALETTE)], cum, end))
        cum = end
    grad = "conic-gradient(" + ", ".join(parts) + ")"
    leg = "".join(
        '<li><i style="background:%s"></i>%s <b>%.1f%%</b></li>'
        % (S_PALETTE[i % len(S_PALETTE)], esc(label), p / total * 100.0)
        for i, (label, p, _) in enumerate(rows))
    aria = "立场占比环形图：" + "，".join("%s %s%%" % (l, round(p / total * 100.0, 1)) for l, p, _ in rows)
    html = ('<div class="donut" role="img" aria-label="%s" style="background:%s;"></div>\n'
            '<ul class="donut-legend">%s</ul>' % (esc(aria), grad, leg))
    return html, True


def _stance_trend(dist, stance_timeline, focus=0):
    """趋势折线 .trend（v0.2.13，静态 SVG）：由 opinion.stance_timeline 计算
    focus 号立场的每日占比坐标（focus 缺省 0 = 占比最高立场）。日期<2 时返回 ("", False)。"""
    if not dist or not stance_timeline:
        return "", False
    order = [str(d.get("stance", "")) for d in dist]
    # v0.3：默认焦点跳过残差立场——否则折线画的是信息量最低的那一支
    if focus < 0 or focus >= len(order) or order[focus] == "未识别":
        focus = next((i for i, n in enumerate(order) if n != "未识别"), 0)
    name = order[focus]
    # 归一：日期升序；以样本量最大的记录日为锚点，仅保留 ±7 天内的活跃日，
    # 剔除孤立背景坐标日（如零散旧帖回潮单日），避免把长历史坐标拉进事件趋势
    raw = {}
    for k, seg in (stance_timeline or {}).items():
        if not isinstance(seg, dict):
            continue
        tot = sum(int(v or 0) for v in seg.values())
        if tot > 0:
            raw[k] = tot
    if not raw:
        return "", False
    anchor = max(raw, key=lambda k: raw[k])
    try:
        anchor_d = date.fromisoformat(str(anchor)[:10])
    except Exception:
        anchor_d = None
    if anchor_d is None:
        dates = sorted(raw)
    else:
        dates = []
        for k in sorted(raw):
            try:
                d0 = date.fromisoformat(str(k)[:10])
            except Exception:
                continue
            if abs((d0 - anchor_d).days) <= 7:
                dates.append(k)
        if not dates:
            dates = sorted(raw)
    if len(dates) < 2:
        return "", False
    vals, weights = [], []
    for day in dates:
        seg = stance_timeline[day] or {}
        tot = sum(int(v or 0) for v in seg.values())
        w = int(seg.get(name) or 0)
        if tot > 0:
            vals.append(w / tot * 100.0)
            weights.append(tot)
        else:
            vals.append(0.0)
            weights.append(0)
    if len(dates) > 12:
        idx = list(range(0, len(dates), int(len(dates) / 11.0) + 1))[:12]
        if idx[-1] != len(dates) - 1:
            idx[-1] = len(dates) - 1
        dates, vals, weights = [dates[i] for i in idx], [vals[i] for i in idx], [weights[i] for i in idx]
    hi = max(max(vals), 10.0)
    ymax = max(40.0, float(int((hi + 9) // 10 * 10)))
    W, H = 560, 220
    x0, x1, y_top, y_bot = 46, 542, 14, 186
    span = y_bot - y_top
    n = len(dates)
    xs = [round(x0 + (x1 - x0) * i / (n - 1), 1) for i in range(n)]
    ys = [round(y_bot - min(v, ymax) / ymax * span, 1) for v in vals]
    pts = " ".join("%s,%s" % (xs[i], ys[i]) for i in range(n))
    color = S_PALETTE[focus % len(S_PALETTE)]
    grid = "".join(
        '<line class="grid-line" x1="%d" y1="%d" x2="%d" y2="%d"></line>' % (x0, y_bot - v / ymax * span, x1, y_bot - v / ymax * span)
        for v in [ymax * k / 4 for k in range(5)])
    glab = "".join('<text x="38" y="%d" text-anchor="end">%d%%</text>' % (y_bot - v / ymax * span + 4, int(round(v)))
                   for v in [ymax * k / 4 for k in range(5)])
    xlab = "".join('<text x="%s" y="208" text-anchor="middle">%s</text>' % (xs[i], str(dates[i])[5:]) for i in range(n))
    dots = "".join('<circle class="dot" cx="%s" cy="%s" r="3.5" fill="%s"></circle>' % (xs[i], ys[i], color) for i in range(n))
    aria = "%s占比趋势：" % name + "，".join("%s %s%%" % (str(dates[i])[5:], round(vals[i], 1)) for i in range(n))
    area_pts = pts + " %s,%s %s,%s" % (x1, y_bot, x0, y_bot)
    html = (
        '<div class="trend">'
        '<div class="trend-head"><span class="trend-title">%s · 当日构成占比</span>'
        '<span class="trend-sub">SVG · 由 stance_timeline 生成（记录日 %s ~ %s）</span></div>'
        '<svg class="trend-svg" viewBox="0 0 %d %d" role="img" aria-label="%s">%s%s'
        '<polygon class="area" points="%s" fill="%s" fill-opacity="0.10"></polygon>'
        '<polyline class="line" points="%s" stroke="%s"></polyline>%s%s</svg>'
        '<ul class="trend-legend"><li><i style="background:%s"></i>%s 当日占比（%%）</li></ul>'
        '<p class="caption">当日占比 = 该立场当日记录数 ÷ 当日总记录数；孤立背景坐标日与无记录日未计入。</p></div>'
        % (esc(name), esc(str(dates[0])), esc(str(dates[-1])), W, H, esc(aria),
           grid, glab,
           area_pts, color,
           pts, color, dots, xlab, color, esc(name)))
    return html, True


def _stance_cards_html(cards):
    """立场卡组 .scards（v0.2.13，视点综合 5.1.2）：结构化卡（卡头名+占比 pill+视角、
    论证链步骤带 .ssteps + 代表性原文 .quote-list + 防呆 .scard-note）。无输入返回 ""。"""
    out = []
    for c in cards or []:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name", "")).strip()
        if not name:
            continue
        pct = str(c.get("pct", "")).strip()
        view = str(c.get("view", "")).strip()
        steps = [str(s).strip() for s in (c.get("steps") or []) if str(s).strip()]
        quotes = []
        for q in c.get("quotes") or []:
            if isinstance(q, dict) and str(q.get("txt", "")).strip():
                quotes.append((str(q.get("like", "")).strip(), str(q.get("txt", "")).strip(),
                               bool(re.search(r"\[\d+\]", str(q.get("ref", ""))))))
        note = str(c.get("note", "")).strip()
        card = ['<article class="scard">',
                '<div class="scard-head"><span class="scard-name">%s</span>' % esc(name)]
        if pct:
            card.append('<span class="scard-pct">%s</span>' % esc(pct))
        if view:
            card.append('<span class="scard-view">%s</span>' % esc(view))
        card.append("</div>")
        if steps:
            # 步骤内 [N] 渲染为可点击引用（v0.3）；书写限制见 report-theme「立场卡组书写限制」
            card.append('<div class="ssteps">' +
                        "".join('<div class="sstep">%s</div>' % _refs_html(s) for s in steps) + "</div>")
        if quotes:
            card.append('<p class="scard-label">代表性原文</p>')
            card.append('<div class="quote-list">' + "".join(
                '<div class="quote-item"><span class="quote-like">%s</span><span class="quote-txt">%s</span></div>'
                % (esc(like or "原文"), esc(txt)) for like, txt, _r in quotes) + "</div>")
        if note:
            card.append('<p class="scard-note">%s</p>' % _refs_html(note))
        card.append("</article>")
        out.append("".join(card))
    if not out:
        return ""
    return '<div class="scards">\n' + "\n".join(out) + "\n</div>"


def _timeline_html(milestones, data_meta=None, day_counts=None):
    """竖式里程碑时间轴（.tl）：milestones=[{date,type,tag,title,text,count,ref}]。
    type ∈ official/media/view/bg/quiet/check；count 缺省不显示采集量；
    ref（如 "[1][5]"）渲染为可点击引用锚点（v0.3）。

    count 回填（v0.4）：②未填 count 时，按该节点日期从 day_counts（opinion.timeline 的
    按日记录数）查回填——"图例声称日计数却不渲染"从此消失，②也不必手工数数。
    图例只描述**实际渲染出来的**节点类型与采集量胶囊：图例声明了却渲染不出的类型，
    会让读者以为报告漏了节点。
    """
    items = []
    used_types, has_count = [], False
    day_counts = day_counts or {}
    for m in milestones or []:
        if not isinstance(m, dict):
            continue
        title = (m.get("title") or "").strip()
        if not title:
            continue
        t = str(m.get("type", "view")).strip() or "view"
        tag = str(m.get("tag", "")).strip() or t
        date = str(m.get("date", "")).strip()
        text = str(m.get("text", "")).strip()
        cnt = m.get("count")
        if not cnt:
            # 回填：取该节点首个日期（区间取起点）对应的当日记录数
            d0 = date.split("~")[0].split("～")[0].strip()
            cnt = day_counts.get(d0) or 0
        cnt_html = ('<span class="tl-cnt">采集 %s 条</span>' % esc(cnt)) if cnt else ""
        has_count = has_count or bool(cnt)
        if t not in used_types:
            used_types.append(t)
        ref_html = _refs_html(m.get("ref"))
        items.append(
            '<div class="tl-item"><div class="tl-date"><b>%s</b>%s</div>'
            '<span class="tl-dot tg-%s"></span>'
            '<div class="tl-body"><span class="tl-tag tg-%s">%s</span>'
            '<strong>%s</strong><p>%s</p>%s</div></div>'
            % (esc(date), cnt_html, esc(t), esc(t), esc(tag), esc(title), esc(text),
               ('<p class="tl-refs">%s</p>' % ref_html) if ref_html else ""))
    if not items:
        return ""
    cn = {"official": "官方行动", "media": "媒体", "view": "表态/解读", "bg": "背景",
          "quiet": "未见表态", "check": "核查"}
    legend = "节点类型：" + " · ".join(cn.get(t, t) for t in used_types)
    if has_count:
        legend += "。日期下的胶囊为当日平台采集记录数。"
    return ('<div class="tl" aria-label="事件时间线">\n' + "\n".join(items) + "\n</div>\n"
            '<p class="legend">%s</p>' % esc(legend))


def sec_timeline(opinion, recs, milestones=None, data_meta=None):
    c = ["<h2>二、事件时间线</h2>"]
    day_counts = {}
    if opinion and isinstance(opinion.get("timeline"), dict):
        day_counts = {str(k)[:10]: v for k, v in opinion["timeline"].items()}
    if not day_counts and recs:
        for r in recs:
            d = str(r.get("time", ""))[:10]
            if len(d) == 10:
                day_counts[d] = day_counts.get(d, 0) + 1
    tl_html = _timeline_html(milestones, data_meta, day_counts)
    if tl_html:
        c.append(tl_html)
        return "\n".join(c)
    tl = {}
    if opinion and opinion.get("timeline"):
        tl = dict(opinion["timeline"])
    else:
        for r in recs:
            t = r.get("time", "")
            if t:
                day = str(t)[:10]
                tl[day] = tl.get(day, 0) + 1
    if not tl:
        c.append('<p>（无时间数据：知乎采集需 crawl.py 输出"发布时间"，外部平台需携带时间字段。请补充后重跑 normalize。）</p>')
    else:
        c.append("<table><thead><tr><th>日期</th><th>记录数</th></tr></thead><tbody>")
        for day in sorted(tl):
            c.append(f"<tr><td>{esc(day)}</td><td>{tl[day]}</td></tr>")
        c.append("</tbody></table>")
    return "\n".join(c)


def sec_facts(facts, extra_md):
    c = ["<h2>三、核心事实汇编</h2>"]
    if facts and facts.get("claims"):
        c.append('<div class="claims">')
        for i, cl in enumerate(facts["claims"], 1):
            title = (cl.get("short_title") or "").strip()
            ev = "、".join(e.get("source", "") for e in cl.get("evidence", [])) or "—"
            # 引用编号（v0.3）：claim.ref 与 evidence[].ref 合并去重，渲染为可点击锚点
            ref_nums, _seen = [], set()
            for _r in [cl.get("ref")] + [e.get("ref") for e in cl.get("evidence", [])
                                         if isinstance(e, dict)]:
                for _m in re.findall(r"\[(\d+)\]", str(_r or "")):
                    if _m not in _seen:
                        _seen.add(_m)
                        ref_nums.append(_m)
            ref_html = " ".join('<a href="#ref%s" class="ref">[%s]</a>' % (n, n) for n in ref_nums)
            vc = _verdict_class(cl.get("verdict", "待核查"))
            ev_lbl = _ev_label(cl.get("evidence_level"))
            raw_v = str(cl.get("verdict", "待核查"))
            # 展示降级：真实 + 证据≤转载层 → 「报道属实（原文未核）」（facts.json 语义不变，见 CONTRACT-joint §2/§3）
            disp_v = ("报道属实（原文未核）" if (raw_v == "真实" and any(k in ev_lbl for k in ("转载", "摘要", "标题层")))
                      else raw_v)
            c.append('<article class="claim">')
            if title:
                c.append('<p class="claim-title">%s</p>' % esc(title))
            c.append('<div class="claim-row"><span class="claim-id">#%d</span>'
                     '<p class="claim-txt">%s</p>'
                     '<span class="v %s">%s</span></div>' % (
                         i, esc(cl.get("claim", "")), vc, esc(disp_v)))
            ev_note = ('<p class="caption">证据等级：%s</p>' % esc(ev_lbl)) if ev_lbl else ""
            ref_line = ('<p class="caption">引用：%s</p>' % ref_html) if ref_html else ""
            c.append('<details class="fold"><summary>说明与证据源</summary>'
                     '<p>%s</p><p class="caption">证据源：%s</p>%s%s</details>'
                     % (esc(cl.get("reason", "")), esc(ev), ev_note, ref_line))
            c.append("</article>")
        c.append("</div>")
    else:
        c.append("<p>未提供事实核查数据（facts.json 缺失或为空）。</p>")
    return "\n".join(c)


def sec_check(facts):
    c = ["<h2>四、事实核查结果</h2>"]
    claims = (facts or {}).get("claims") or []
    if not claims:
        c.append("<p>无核查记录。</p>")
        return "\n".join(c)

    c.append('<div class="meta-box"><h3 style="margin-top:0">本章与第三章的分工</h3><dl>'
             '<dt>第三章</dt><dd>逐条汇编：断言原文 + 完整说明 + 证据源（折叠，保留全文）</dd>'
             '<dt>本章</dt><dd>裁决压缩视图：结论分布 / 分组速览 / 口径冲突（引用第三章编号，不重复全文）</dd>'
             '<dt>裁决依据</dt><dd>china_sources 初查 ＋ LLM 多源交叉（详见第九章）</dd></dl></div>')

    def _cat(cl):
        """裁决分组。裁决词只有四个（CONTRACT-joint §3）；v0.5 删掉了「真实（观点性判断）」分支——
        它没有生产者（观点性主张本就不进断言登记册，见 §6），留着只会诱导模型自造裁决词。"""
        v = str(cl.get("verdict", ""))
        if "部分" in v:
            return ("part", "部分真实")
        if "失实" in v:
            return ("false", "失实")
        if "证据不足" in v or "不足" in v or v in ("待核查", "无法核实"):
            return ("none", "证据不足")
        return ("true", "真实")

    groups = {}
    for i, cl in enumerate(claims, 1):
        key, label = _cat(cl)
        groups.setdefault(key, {"label": label, "ids": [], "cls": {
            "true": "v-true", "part": "v-part",
            "false": "v-false", "none": "v-none"}[key]})
        groups[key]["ids"].append("#%d" % i)
    pills = []
    for key in ("true", "part", "false", "none"):
        g = groups.get(key)
        if g:
            pills.append('<span class="vpill %s">%s <span class="n">%d</span></span>'
                         % (g["cls"], esc(g["label"]), len(g["ids"])))
    c.append('<div class="vpills">' + "".join(pills) + "</div>")
    c.append("<h3>按结论分组速览</h3>")
    descr = {
        "true": "—— 证据多源一致支持；其中证据≤转载层者徽章显示为「报道属实（原文未核）」（语义仍为真实，见第三章折叠说明）。",
        "part": "—— 要素部分可证、部分存出入或未逐一核验（口径冲突见下表）。",
        "false": "—— 与可靠信源相悖或已被否定。",
        "none": "—— 无具名信源/检索未见/无法核验（含否定性主张的默认裁决）；不等于确认不存在或确认失实。",
    }
    for key in ("true", "part", "false", "none"):
        g = groups.get(key)
        if g:
            c.append('<p><strong><span class="v %s">%s</span>（%d 条）</strong>：%s%s</p>'
                     % (g["cls"], esc(g["label"]), len(g["ids"]),
                        "、".join(g["ids"]), descr[key]))
    # 口径冲突（兼容 conflicts: {a,b,note} 与 {issue,verdict,note} 两种 schema）
    def _cap(src):
        return ('<br><span class="caption">%s</span>' % esc(src)) if src else ""
    cf_rows = []
    for i, cl in enumerate(claims, 1):
        cf = cl.get("conflicts")
        if isinstance(cf, dict):
            # 契约形态（CONTRACT-joint §11，v0.5 定死）：{point, versions:[{who,value}], resolution}
            if "versions" in cf or "point" in cf:
                vers = [v for v in (cf.get("versions") or []) if isinstance(v, dict)]
                cells = [esc(str(v.get("value", ""))) + _cap(v.get("who", "")) for v in vers]
                a = cells[0] if cells else ""
                b = " ／ ".join(cells[1:]) if len(cells) > 1 else ""
                note = esc(cf.get("point", ""))
                res = esc(cf.get("resolution", ""))
                cf_rows.append((a, b, esc(cl.get("verdict", "")),
                                (note + ("：" if note and res else "") + res), "#%d" % i))
            # 兼容旧形态：{a,b,note} / {issue,verdict,note}
            elif cf.get("a") or cf.get("b"):
                a = (cf.get("a") or {}); b = (cf.get("b") or {})
                cf_rows.append((esc(a.get("claim", "")) + _cap(a.get("src", "")),
                                esc(b.get("claim", "")) + _cap(b.get("src", "")),
                                esc(cl.get("verdict", "")), esc(cf.get("note", "")), "#%d" % i))
            elif cf.get("issue"):
                cf_rows.append((esc(cf.get("issue", "")), "", esc(cf.get("verdict", "")),
                                esc(cf.get("note", "")), "#%d" % i))
            else:
                # 兜底：形态不识别也不再静默丢弃（门禁 G13 会把它判为 error）
                cf_rows.append(("（形态未识别）", "", esc(cl.get("verdict", "")),
                                "键：" + esc("、".join(list(cf.keys())[:4])), "#%d" % i))
    if cf_rows:
        c.append("<h3>口径冲突与悬置（需留意）</h3>")
        c.append('<table class="conflict"><thead><tr><th>A 说法</th><th>B 说法</th><th>裁决</th><th>说明</th><th>来源</th></tr></thead><tbody>')
        for a, b, v, n, ref in cf_rows:
            c.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (a, b, v, n, ref))
        c.append("</tbody></table>")
    c.append('<p class="legend">裁决口径：真实=证据多源一致支持；部分真实=部分可证/口径出入；失实=与可靠信源相悖；证据不足=无法核验或否定性主张默认（不反向推论）。逐条证据等级与理由见第三章折叠说明。</p>')
    c.append("<h3>核查方法与证据链</h3>")
    c.append("<ul><li><strong>初查</strong>：china_sources（辟谣平台/澎湃站内检索）＋ WebSearch 多源检索建立证据池；官方文件与平台一手材料（法条全文、原帖）优先。</li>"
             "<li><strong>交叉</strong>：多篇独立报道与原始材料逐条比对，登记证据等级 1-4（共 %d 条断言）；转载层/摘要层证据不单独支撑「真实」，展示按「报道属实（原文未核）」降级。</li>"
             "<li><strong>用词纪律</strong>：正文区分事实/解读/流传说法；无具名信源的「网传」叙事不采为事实；否定性主张（未见/未回应）一律证据不足并注明检索边界。</li></ul>" % len(claims))
    return "\n".join(c)


def _cards_from_opinion(dist):
    """无 --stance-cards 结构化输入时的程序化回退：按立场生成基础卡组
    （卡头 名+占比+视角 ｜ 代表性原文 .quote-list ｜ 防呆 note）。
    『未识别』（信息帖/提问）不设卡，论证链步骤需结构化输入提供。"""
    cards = []
    for d in dist or []:
        name = str(d.get("stance", "")).strip()
        if not name or name == "未识别":
            continue
        pct = float(d.get("pct") or 0)
        cnt = int(d.get("count") or 0)
        minority = bool(d.get("minority"))
        samples = (d.get("top_samples") or [])[:3]
        quotes = []
        for s in samples:
            if not isinstance(s, dict):
                continue
            like = str(s.get("likes") or "")
            txt = _clip_text(s.get("content", ""), 120)
            if txt:
                quotes.append({"like": ("赞%s" % like) if like else "原文", "txt": txt})
        note = None
        if minority:
            note = "少数派：占比 <10%，样本有限；引用时须标注信源稀缺，不作群体代表性推断。"
        cards.append({
            "name": name,
            "pct": ("%.1f%% · %d 条" % (pct, cnt)) if cnt else ("%.1f%%" % pct),
            "view": "少数派 · 样本稀缺" if minority else "主要声量立场",
            "quotes": quotes,
            "note": note,
        })
    return cards


def sec_opinion_analysis(facts):
    """五、观点辨析与推理说明（v0.5）——verify 模式第 6 章在联合态的落点。

    数据源：facts.json `opinion_analysis`（③ 破虚妄产出，schema 见 CONTRACT-joint §11）。
    此前联合报告里这一章完全没有位置：③ 的"隐含前提/逻辑谬误/立场偏差"只能塞进 `reason`，
    实测在报告中出现 0 次——能力被静默丢弃。字段为空时本节照实说明"未产出"，不生成空壳卡片。
    """
    items = [x for x in ((facts or {}).get("opinion_analysis") or []) if isinstance(x, dict)]
    c = ["<h2>五、观点辨析与推理说明</h2>"]
    if not items:
        c.append('<p>本次未产出观点辨析（<code>facts.json.opinion_analysis</code> 为空或未适用）。'
                 '本节由 ③ 破虚妄登记：辨析对象为**主张本身**的隐含前提、逻辑谬误与立场偏差，'
                 '与第五、六章记录的**公众立场分布**是两件事，不可互相顶替。</p>')
        return "\n".join(c)
    c.append('<p class="chapter-intro">本节辨析的对象是<strong>主张的论证结构</strong>'
             '（隐含前提 / 逻辑谬误 / 立场偏差 / 从证据到结论的推导链），'
             '不重复第三章的事实裁决，也不重复第六章的公众立场分布。</p>')
    c.append('<div class="claims">')
    for k, it in enumerate(items, 1):
        vid = str(it.get("id") or ("V%d" % k)).strip()
        c.append('<article class="claim">')
        target = str(it.get("target", "")).strip()
        if target:
            c.append('<p class="claim-title">%s</p>' % esc(target))
        cl_html = ""
        raw_claims = it.get("claims")
        if isinstance(raw_claims, (list, tuple)) and raw_claims:
            cl_html = " ".join('<a href="#ch3">#%s</a>' % esc(x) for x in raw_claims)
        c.append('<div class="claim-row"><span class="claim-id">%s</span>'
                 '<p class="claim-txt">%s</p></div>' % (esc(vid), cl_html))
        rows = []
        for label, key in (("隐含前提", "premise"), ("逻辑谬误", "fallacy"), ("立场偏差", "bias")):
            v = str(it.get(key, "") or "").strip()
            if v:
                rows.append("<dt>%s</dt><dd>%s</dd>" % (label, esc(v)))
        chain = str(it.get("chain", "") or "").strip()
        concl = str(it.get("conclusion", "") or "").strip()
        if chain:
            rows.append("<dt>推导链</dt><dd>%s</dd>" % esc(chain))
        if concl:
            rows.append("<dt>结论</dt><dd>%s</dd>" % esc(concl))
        if rows:
            c.append('<details class="fold"><summary>论证结构与推导链</summary><dl>%s</dl></details>'
                     % "".join(rows))
        c.append("</article>")
    c.append("</div>")
    c.append('<p class="legend">辨析对象是论证而非事实：本章不改变第三章的裁决词，'
             '也不把"逻辑有问题"读成"所述失实"。</p>')
    return "\n".join(c)


def sec_opinion(opinion, viewpoint_md, stance_cards=None, url_ref=None):
    c = ["<h2>六、舆论观点综合</h2>"]
    url_ref = url_ref or {}
    if opinion and opinion.get("stance_distribution"):
        dist = opinion["stance_distribution"]
        # 5.1 双栏：左条形（读精确值）右环形（看占比直觉）——v0.2.13 .chart-split/.donut
        bars_html = ['<div class="bars" aria-label="立场占比条形图">']
        for d in dist:
            pct = d.get("pct", 0) or 0
            bars_html.append('<div class="bar-row"><span class="bar-label">%s</span>'
                             '<span class="bar-track"><span class="bar-fill" style="width:%s%%"></span></span>'
                             '<span class="bar-val">%s%% · %s 条</span>%s</div>'
                             % (esc(d.get("stance", "")), pct, pct, d.get("count", 0),
                                '<span class="mark mark-single">少数派</span>' if d.get("minority") else ""))
        bars_html.append("</div>")
        donut_html, donut_ok = _stance_donut(dist)
        if donut_ok:
            c.append('<div class="chart-split">\n  <div>\n%s\n  </div>\n  <div>\n%s\n'
                     '  <p class="legend">环形图与左侧条形图为同一占比的两种编码（生成端按数据自动计算）。</p>'
                     '\n  </div>\n</div>' % ("\n".join(bars_html), donut_html))
        else:
            c.append("\n".join(bars_html))
        # 归类覆盖率与残差结构（v0.3，v0.4 加"可判集合"分母与残差三段）
        _cov = opinion.get("coverage_rate")
        if _cov is not None:
            _res = opinion.get("residual") or {}
            _inst = (opinion.get("institutional_layer") or {}).get("count", "—")
            _covj = opinion.get("coverage_of_judgeable")
            comp = _res.get("composition") or {}
            line = ('<p class="stat-line">立场分母 <strong>%s</strong> 条（个人表达）｜归类覆盖率 '
                    '<strong>%s%%</strong>（占全部语料）' % (opinion.get("denominator", "—"), _cov))
            if _covj is not None:
                line += '·<strong>%s%%</strong>（占可判集合 %s 条）' % (_covj, opinion.get("judgeable_denominator", "—"))
            line += '｜机构/媒体帖 %s 条 · 未识别 %s 条' % (_inst, _res.get("count", "—"))
            if comp:
                def _seg(*names):
                    for nm in names:
                        if nm in comp:
                            return comp[nm]
                    return "—"
                line += '（纯反应 %s · 原样转述 %s · 未分类 %s）' % (
                    _seg("纯反应（短句/表情/链接）", "纯反应"), _seg("原样转述（长文报道口径）", "原样转述"),
                    _seg("未分类（待抽样人工核）", "待语义判读", "未分类"))
            line += '——立场占比只读作「可识别立场内部的相对结构」，不代表全量舆论分布</p>'
            c.append(line)
        # 高赞代表 → 表格（v0.2.13：cell 内 .quote-list/.quote-item 逐条成块）
        # v0.3：样本按 ref_url 自动挂号（[N]），索引在 ch5 之前已算好并传入
        c.append('<div class="table-scroll"><table><thead><tr><th>立场</th><th>占比</th><th>条数</th>'
                 '<th>少数派</th><th>赞同占比</th><th>高赞代表（前3）</th></tr></thead><tbody>')
        for d in dist:
            parts = []
            for s in d.get("top_samples", []):
                if not isinstance(s, dict):
                    continue
                u = str(s.get("ref_url") or s.get("url") or "").strip()
                n = url_ref.get(u)
                chip = (' <a href="#ref%s" class="ref">[%s]</a>' % (n, n)) if n else ""
                parts.append('<div class="quote-item"><span class="quote-like">赞%s</span>'
                             '<span class="quote-txt">%s</span>%s</div>'
                             % (esc(str(s.get("likes", ""))),
                                esc(_clip_text(s.get("content", ""), 90)), chip))
            tops = "".join(parts) or '<span class="quote-txt">—</span>'
            like_pct = ("%s%%" % d["like_pct"]) if d.get("like_pct") is not None else "—"
            c.append('<tr><td>%s</td><td>%s%%</td><td>%d</td><td>%s</td><td>%s</td>'
                     '<td><div class="quote-list">%s</div></td></tr>'
                     % (esc(d["stance"]), d["pct"], d["count"],
                        "是" if d.get("minority") else "—", like_pct, tops))
        c.append("</tbody></table></div>")
        # 5.2 焦点转移双栏：左堆叠（当日各立场条数）右趋势折线（focus=0 主立场当日占比）——v0.2.13
        st = opinion.get("stance_timeline")
        if isinstance(st, dict) and st:
            order = [d["stance"] for d in dist]
            stacks_html = ['<div class="stacks" aria-label="焦点转移（立场×日期）">']
            for day in sorted(st):
                segs = []
                for i, sname in enumerate(order, 1):
                    n = (st[day] or {}).get(sname, 0)
                    if n:
                        segs.append('<span class="stack-seg s%d" style="flex:%d"></span>' % (i, n))
                stacks_html.append('<div class="stack-row"><span class="stack-date">%s</span>'
                                   '<span class="stack-track">%s</span></div>' % (esc(day), "".join(segs)))
            stacks_html.append('<div class="stack-legend">')
            for i, sname in enumerate(order, 1):
                stacks_html.append('<span><i class="s%d"></i>%s</span>' % (i, esc(sname)))
            stacks_html.append("</div></div>")
            # 趋势折线只在覆盖率足够时才画（v0.4）：分子残缺时画的折线既不是舆论趋势、
            # 也不是每日相对结构，属于"用错误的数做正确的图"。
            cov_now = float(opinion.get("coverage_of_judgeable") or opinion.get("coverage_rate") or 0)
            if cov_now < 50:
                c.append("\n".join(stacks_html))
                c.append('<p class="legend">未渲染「%s」占比趋势折线：当前归类覆盖率仅 %.1f%%'
                         '（可判集合口径），分子残缺时折线的走势不代表舆论变化。'
                         '补足判据（词典层或规则层）后重跑即可恢复。</p>' % (order[0] if order else "主立场", cov_now))
                trend_ok = False
            else:
                trend_html, trend_ok = _stance_trend(dist, st, focus=0)
            if trend_ok:
                c.append('<div class="chart-split">\n  <div>\n%s\n  </div>\n  <div>\n%s\n'
                         '  </div>\n</div>' % ("\n".join(stacks_html), trend_html))
            elif cov_now >= 50:
                c.append("\n".join(stacks_html))
        elif opinion.get("timeline"):
            c.append("<p>焦点时间分布见第二章时间线。</p>")
    else:
        c.append("<p>未提供舆论聚类数据（opinion.json 缺失或为空）。</p>")
    # 立场卡组 + 论证链带（5.1.2，v0.2.13）：结构化卡；无 --stance-cards 时按 opinion 程序化回退
    cards = []
    if stance_cards:
        cards = [x for x in stance_cards if isinstance(x, dict)]
    if not cards and opinion and opinion.get("stance_distribution"):
        # v0.3：缺 --stance-cards 工件时不再回退生成「引语卡」——那会把本章「高赞代表」表的
        # 引语在卡内重复一遍（同一材料出现两次）。按契约「缺工件宁可不渲染」，只给一行提示。
        c.append('<p class="legend">（未提供 --stance-cards 立场卡组工件：按契约不回退生成引语卡，'
                 '以免与本章「高赞代表」表重复。卡组应由阶段②产出 stance_cards.json，'
                 '内容为抽象逻辑链而非引语。）</p>')
    cards_html = _stance_cards_html(cards)
    if cards_html:
        c.append('<h3>各立场论证结构与代表性论点（立场卡组）</h3>')
        c.append('<p class="legend">卡头 = 立场名 + 占比 + 视角；论证链步骤带自动编号；代表性原文来自语料高赞样本'
                 '（截断节选，全文见来源索引）；防呆行保留少数派/反讽警示。无独立论证的「未识别（信息帖/提问）」不设卡。</p>')
        c.append(cards_html)
    # 人工抽检结果（v0.4）：占比必须与误判率并排——这一节没有，占比就只是个未校正的裸数字
    sc_html = _spotcheck_html((opinion or {}).get("spotcheck"), opinion)
    if sc_html:
        c.append(sc_html)
    if viewpoint_md:
        vmd = md_to_html(viewpoint_md)
        if vmd.lstrip().startswith("<h"):
            # 模板 v0.2.13：h3 后紧跟 .viewpoint-meta 即视为视点综合小节（h3 保持 <main> 直接子级以保留自动编号）
            m = re.match(r"(<h3[^>]*>.*?</h3>)(.*)$", vmd, re.S)
            if m:
                vmd = m.group(1) + _viewpoint_meta(opinion) + "\n" + m.group(2)
        else:
            c.append("<h3>视点综合（LLM 分析）</h3>")
            c.append(_viewpoint_meta(opinion))
        c.append(vmd)
    else:
        c.append('<p class="legend">（未提供视点综合 markdown：建议 LLM 按 opinion.md 提炼各立场论证结构、标注少数派与对立观点、判断焦点转移后以 --viewpoint 注入。）</p>')
    return "\n".join(c)


def _viewpoint_meta(opinion):
    """视点综合信息条（v0.2.13 .viewpoint-meta/.viewpoint-snap）：动态快照徽章 + 一句说明。"""
    day = ""
    if opinion:
        st = opinion.get("stance_timeline") or {}
        tl = sorted(st.keys()) if st else []
        if tl:
            day = str(tl[-1])[:10]
    snap = ("动态快照 · %s" % day) if day else "动态快照"
    return ('<div class="viewpoint-meta"><span class="viewpoint-snap">%s</span>'
            '<span>以下为 LLM 对各立场论证结构、少数派与对立观点、焦点转移的视点综合；数据与比例以本章上方聚类为准。</span></div>'
            % esc(snap))


def _cited_sources(opinion):
    """由 opinion 的高赞样本收集「被引用地址」，供索引优先收录（v0.3）。

    样本的 ref_url 由 opinion.py 解析：自身 url 优先，评论无永久链接时回指其父帖。
    """
    out = []
    for d in ((opinion or {}).get("stance_distribution") or []):
        for s in d.get("top_samples") or []:
            u = str(s.get("ref_url") or s.get("url") or "").strip()
            if not u:
                continue
            pn = PLATFORM_NAMES.get(s.get("platform"), s.get("platform") or "平台")
            kind = ("高赞样本帖（被引用评论所在帖）" if s.get("ref_how") == "parent-post"
                    else "高赞样本")
            out.append({"name": "%s：%s" % (pn, kind), "url": u, "grade": "C",
                        "type": "平台语料样本（评论引用回指其父帖）"
                                if s.get("ref_how") == "parent-post" else "平台语料样本"})
    return out


def _split_org_title(nm):
    """把来源名拆成（机构/平台, 标题）两列。

    形如「人民网·观点频道《人民锐评：四岁男童的肢体接触…》」→
    （人民网·观点频道, 人民锐评：四岁男童的肢体接触…）。
    书名号内的冒号/竖线是标题的一部分，不是列分隔符（v0.4 修表格裂行）。
    """
    nm = str(nm or "").strip()
    if not nm:
        return "", ""
    m = re.search(r"[《「](.+?)[》」]", nm)
    if m:
        org = (nm[:m.start()] or nm[m.end():]).strip(" 　·—-｜|：:")
        return org or nm[:m.start()].strip(), m.group(1).strip()
    for sep in ("：", ":", "｜", "|"):
        if sep in nm:
            org, title = nm.split(sep, 1)
            return org.strip(), title.strip()
    return nm, ""


def _collect_sources(sources, recs, cited=None):
    """合并显式来源、被正文引用的样本地址与自动收集的 URL（去重）。

    自动部分的取法（v0.4 改）：按平台分层轮流取，不再"按记录顺序取前 50 条"——
    旧实现让来源索引结构性偏向微博帖文，评论/知乎/小红书样本无从回溯。
    """
    srcs = list(sources or [])
    seen = {str(s.get("url", "")).strip() for s in srcs if str(s.get("url", "")).strip()}
    for it in (cited or []):
        u = str(it.get("url", "")).strip()
        if u and u not in seen:
            seen.add(u)
            srcs.append({"name": it.get("name") or "平台语料样本", "url": u,
                         "grade": it.get("grade", "C"), "type": it.get("type", "平台语料样本")})
    # 分层轮转：各平台各自成队，按队轮流取，直到额度用尽
    queues = {}
    for r in recs:
        u = r.get("url", "")
        if not u or u in seen:
            continue
        seen.add(u)
        queues.setdefault(r.get("platform", "unknown"), []).append(r)
    auto_n, cap = 0, 50
    while auto_n < cap and any(queues.values()):
        for p in list(queues):
            if not queues[p]:
                continue
            r = queues[p].pop(0)
            srcs.append({"name": "%s·%s" % (PLATFORM_NAMES.get(p, p), r.get("author", "")),
                         "url": r.get("url", ""), "grade": "C"})
            auto_n += 1
            if auto_n >= cap:
                break
    return srcs


def _spotcheck_html(spotcheck, opinion=None):
    """人工抽检结论小节（v0.4）：占比必须与误判率并排，未识别层须给"含立场表达"的估计。

    spotcheck 可以是 {kind: result} 字典（来自 opinion.json 的 spotcheck 字段），
    也可以是单份 spotcheck.json（含 kind/status）。
    """
    if not spotcheck:
        return ""
    if "kind" in spotcheck:
        spotcheck = {spotcheck.get("kind", "stance"): spotcheck}
    done = {k: v for k, v in spotcheck.items() if isinstance(v, dict) and v.get("status") == "已完成"}
    if not done:
        return ('<h3>人工抽检（未完成）</h3><p class="legend">占比为未校正读数：'
                '尚未完成人工抽检（见 <code>spotcheck.py</code>），本章百分比不带误判率区间。</p>')
    c = ["<h3>人工抽检结果</h3>",
         '<p class="legend">口径：固定随机种子分层抽样（平台 × 点赞档），逐条人工判读；'
         '抽样方法与结论均存于 <code>spotcheck.json</code>，任何人可用同一种子复现同一批样本。</p>']
    bad = []
    for kind, res in done.items():
        if kind in ("stance", "rule"):
            label = "立场标签" if kind == "stance" else "规则层（口语判据）"
            c.append('<table><thead><tr><th>%s · 立场</th><th>抽样</th><th>误判</th><th>误判率</th>'
                     '<th>可否作为主结论</th></tr></thead><tbody>' % label)
            for st, v in (res.get("per_stance") or {}).items():
                ok = v.get("usable_as_main")
                if not ok:
                    bad.append(st)
                c.append('<tr><td>%s</td><td>%d</td><td>%d</td><td>%.1f%%</td><td>%s</td></tr>'
                         % (esc(st), v["n"], v["wrong"], v["error_rate"],
                            "可" if ok else '<strong>不可（>30%）</strong>'))
            c.append('</tbody></table>')
            c.append('<p class="legend">%s 核总体误判率 <strong>%.1f%%</strong>（共判读 %d 条）。</p>'
                     % (label, res.get("overall_error_rate", 0), res.get("n_items", 0)))
        else:
            c.append('<p><strong>未识别层抽样</strong>：从未识别层 %s 条中抽 %s 条人工判读，'
                     '其中 <strong>%s 条含立场表达（%.1f%%）</strong>，%s 条不含（纯反应/玩笑/信息）。'
                     '按层配额加权回推：<strong>未识别层约 %s 条含立场表达</strong>'
                     '（95%% 置信区间 %s 条）——即本章占比的绝对水平被系统性低估，'
                     '只能读作「已识别部分内的结构」。</p>'
                     % (res.get("pool_size", "—"), res.get("n_items", "—"), res.get("with_stance", "—"),
                        res.get("p_with_stance", 0), res.get("without_stance", "—"),
                        res.get("estimated_with_stance_in_pool", "—"),
                        "–".join(str(x) for x in (res.get("estimated_total_range") or ["—", "—"]))))
            mix = res.get("stance_mix_in_sample") or {}
            if mix:
                c.append('<p class="legend">样本内构成：%s（据此可知被漏掉的主要是哪一支）。</p>'
                         % "、".join("%s %d 条" % (esc(k), v) for k, v in mix.items()))
    if bad:
        c.append('<p class="legend">⚠ 抽检发现以下立场误判率 >30%%，<strong>不得作为主结论呈现</strong>：%s。</p>'
                 % "、".join(esc(x) for x in sorted(set(bad))))
    return "\n".join(c)


def sec_sources(srcs):
    c = ["<h2>七、来源索引</h2>"]
    if not srcs:
        c.append("<p>无来源记录。</p>")
    else:
        # 分级小结（sources 条目可选 grade 字段；全缺省则只报总数，不臆造分级）
        grades = {"A": 0, "B": 0, "C": 0}
        for s in srcs:
            g = str(s.get("grade", "")).strip().upper()
            if g in grades:
                grades[g] += 1
        bits = ["来源共 <strong>%d</strong> 条" % len(srcs)]
        if any(grades.values()):
            bits.append("A官方 %d · B主流 %d · C自媒体+样本 %d" % (grades["A"], grades["B"], grades["C"]))
        c.append('<p class="stat-line">%s</p>' % " ｜ ".join(bits))
        c.append('<div class="table-scroll"><table><thead><tr><th>编号</th><th>来源 / 平台</th><th>标题摘要</th>'
                 '<th>分级</th><th>原文</th></tr></thead><tbody>')
        gname = {"A": "A 官方/权威", "B": "B 主流", "C": "C 自媒体/样本"}  # 显示名；分级口径见 CONTRACT-joint §2
        for i, s in enumerate(srcs, 1):
            g = str(s.get("grade", "")).strip().upper()
            cls = ' class="source-%s"' % g if g in ("A", "B", "C") else ""
            # 来源/标题拆列（v0.4 修）：先认《》配对，书名号内的「：/｜」不是列分隔符——
            # 旧实现按第一个冒号硬切，`《人民锐评：四岁男童的肢体接触…》` 会把标题切进来源列，表格裂行。
            org, title = _split_org_title(str(s.get("name", "")).strip())
            if not title:
                title = "（无标题摘要）"
            url = str(s.get("url", "")).strip()
            link_html = ('<a href="%s" target="_blank" rel="noopener">原文 ↗</a>' % esc(url)) if url else "—"
            # 归档标记（v0.4，CONTRACT-joint §11）：②若给了 fetched_file，说明该源正文已落盘可回溯。
            # 不加列（模板表头固定 5 列），用 title 属性承载，零破坏。
            ff = str(s.get("fetched_file", "")).strip()
            if ff:
                link_html += '<span class="ref" title="已归档正文：sources/%s" aria-label="已归档">⌸</span>' % esc(ff)
            c.append('<tr id="ref%d"%s><td>[%d]</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'
                     % (i, cls, i, esc(org), esc(title), gname.get(g, g), link_html))
        c.append("</tbody></table></div>")
        # 复用度（v0.4）：只露一次面的来源数——"来源共 N 条"会被读成举证面很宽，需给出分布
        used = [len([x for x in (s.get("used_as") or [])]) for s in srcs]
        if any(used):
            once = sum(1 for u in used if u <= 1)
            many = sum(1 for u in used if u >= 5)
            c.append('<p class="legend">来源复用度：被引用 ≥5 次 %d 条 · 仅 1 次 %d 条（引用集中度越高，'
                     '越应核对该来源是否为转载簇的同一稿）。</p>' % (many, once))
    return "\n".join(c)


def sec_coverage(facts, recs, coverage_md, compliance=None):
    c = ["<h2>八、覆盖完整性声明</h2>"]
    counts = _platform_counts(recs)
    # 覆盖缺口矩阵（程序可判定部分：平台采集状态 / 断言证据覆盖；文字缺口仍靠 --coverage）
    c.append('<table class="matrix"><thead><tr><th>平台/信源</th><th>状态</th><th>说明</th></tr></thead><tbody>')
    for p, name in PLATFORM_NAMES.items():
        if p == "unknown":
            continue
        if counts.get(p):
            c.append('<tr><td>%s</td><td class="cell-ok">已采集 %d 条</td>'
                     '<td>数据已归一化并参与聚类/核查</td></tr>' % (name, counts[p]))
        else:
            c.append('<tr><td>%s</td><td class="cell-miss">未采集</td>'
                     '<td>凭证缺失/未部署/未请求，需在采集阶段确认原因</td></tr>' % name)
    if facts and facts.get("claims"):
        no_ev = sum(1 for cl in facts["claims"] if not cl.get("evidence"))
        ev_cls = "cell-gap" if no_ev else "cell-ok"
        ev_txt = "%d 条断言中 %d 条无自动证据" % (len(facts["claims"]), no_ev) if no_ev else "全部断言均有证据记录"
        c.append('<tr><td>事实证据</td><td class="%s">%s</td><td>%s</td></tr>'
                 % (ev_cls, ev_txt, "无自动证据的断言依赖 LLM 多源核查（见第四节）" if no_ev else ""))
    c.append("</tbody></table>")
    if compliance:
        c.append("<h3>联合任务合规账本</h3>")
        c.append('<table class="compliance"><thead><tr><th>平台</th><th>状态</th><th>阻隔</th>'
                 '<th>已升级重试</th><th>用户确认</th><th>替代路径</th><th>备注</th></tr></thead><tbody>')
        for row in compliance:
            c.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                     % tuple(esc(row.get(k, "")) for k in
                             ("platform", "status", "block", "escalated", "user_confirmed", "alternative", "note")))
        c.append("</tbody></table>")
        c.append('<p class="legend">合规账本依 CONTRACT-joint：状态非「已采」且无用户确认即不合规（门禁 G1）。</p>')
    c.append('<p class="legend">色块：绿=已覆盖 ｜ 灰=未采集/未部署 ｜ 橙=存在缺口。矩阵只表达程序可判定的覆盖状态；立场缺口、未回应方等文字性缺口见下方 LLM 补充。</p>')
    if coverage_md:
        c.append(md_to_html(coverage_md))
    else:
        c.append('<p class="legend">（建议 LLM 补充：未采到的立场/未回应的涉事方/信源稀缺度标注，以 --coverage 注入。）</p>')
    return "\n".join(c)


def sec_method():
    return ("<h2>九、方法论</h2>"
            "<p>本报告由 bai-bao-dai（百宝袋）四阶段流水线生成：</p>"
            "<ol><li><strong>采集</strong>：知乎内嵌爬虫 / MediaCrawler（微博·小红书）关键词搜索，仅限公开信息；</li>"
            "<li><strong>事实还原</strong>：多源交叉与证据分级（china_sources 初查 + LLM 逐条裁决：真实/部分真实/失实/证据不足）；</li>"
            "<li><strong>舆论把握</strong>：按立场词典单标签聚类（命中关键词最多者胜），识别 &lt;10% 少数派；</li>"
            "<li><strong>综合报告</strong>：本 HTML 九章结构。</li></ol>"
            '<p class="legend">确定性标记：▲官方确认 / ●多源一致 / △单源存疑。未确认信息不伪装成事实。</p>')


def sec_timestamp(recs):
    c = ["<h2>十、信息与生成时间戳</h2>", '<div class="meta-box"><dl>']
    c.append(f"<dt>报告生成</dt><dd>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</dd>")
    times = [str(r.get("time", ""))[:10] for r in recs if r.get("time")]
    if times:
        c.append(f"<dt>信息截止</dt><dd>{esc(max(times))}（最后一条采集记录时间；搜索引擎索引有延迟，结论可能滞后）</dd>")
    else:
        c.append("<dt>信息截止</dt><dd>未知（数据缺时间戳）</dd>")
    c.append("</dl></div>")
    return "\n".join(c)


def _read_md(path):
    """读取 markdown 文件内容；缺失返回空串。"""
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                return f.read()
        except Exception as e:
            print(f"[report] 警告：markdown 读取失败 {path}：{e}")
    return ""


# ---------- P2 可选组件（--actors / --track；无输入一律不渲染） ----------
_MARK_CLASS = {"▲": "mark-official", "●": "mark-multi", "△": "mark-single", "✗": "mark-caution", "?": "mark-single"}


def _mark_html(text):
    """把 '▲官方' 等语义文本渲染为确定性徽章；空返回空串。"""
    text = str(text or "").strip()
    if not text:
        return ""
    for ch, cls in _MARK_CLASS.items():
        if ch in text:
            return '<span class="mark %s">%s</span>' % (cls, esc(text))
    return ""


def _refs_html(ref):
    """把 '[1][4]' 样式文本中的编号转为可点击引用锚点，其余原样转义。"""
    s = str(ref or "")
    if not s:
        return ""
    parts, last = [], 0
    for m in re.finditer(r"\[(\d+)\]", s):
        if m.start() > last:
            parts.append(esc(s[last:m.start()]))
        parts.append('<a href="#ref%s" class="ref">[%s]</a>' % (m.group(1), m.group(1)))
        last = m.end()
    if last < len(s):
        parts.append(esc(s[last:]))
    return "".join(parts)


# ---------- 主体轨道网格（--actors v2 对象形态，v0.2.18）----------
_KINDS = ("act", "decl", "view", "judge", "quiet")


def _node_col(nd, stages):
    """节点所在列（1-based）：优先 col 整数，否则按 stage 名（精确→包含）匹配。"""
    v = nd.get("col")
    if v is not None:
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    st = str(nd.get("stage", "") or "").strip()
    if not st:
        return None
    for i, s in enumerate(stages, 1):
        if st == str(s.get("name", "")).strip():
            return i
    for i, s in enumerate(stages, 1):
        if st in str(s.get("name", "")):
            return i
    return None


def _rn_html(nd):
    """轨道节点胶囊 .rn.<kind>（act ●官方行动 / decl ▲声明发布 / view ○表态·被指 / judge ◈裁决 / quiet ┄静默）。"""
    kind = str(nd.get("kind", "act") or "act").strip().lower()
    if kind not in _KINDS:
        kind = "act"
    txt = " ".join(x for x in (str(nd.get("date", "") or "").strip(),
                               str(nd.get("text", "") or "").strip()) if x)
    if not txt:
        return ""
    return '<span class="rn %s">%s</span>' % (kind, esc(txt))


def _claims_html(claims, verdicts=None):
    """主张挂接：接受 [1,2] 或 [{"n":1,"verdict":"真实"}] → #N（跳第三章）+ 裁决徽章。
    verdicts = {断言序号: 裁决词}（由 build_content 从 facts.json 登记册构建）：
    本处未显式给 verdict 时自动补齐——保证「裁决词以登记册为唯一来源」（CONTRACT-joint §9）。"""
    items = []
    for c in claims or []:
        vd = ""
        if isinstance(c, dict):
            n = c.get("n") or c.get("id") or ""
            vd = str(c.get("verdict", "") or "").strip()
        else:
            n = c
        num = str(n or "").strip().lstrip("#").strip()
        if not num:
            continue
        if not vd and verdicts:
            try:
                vd = str(verdicts.get(int(num), "") or "").strip()
            except (TypeError, ValueError):
                vd = ""
        frag = '<a href="#ch3">#%s</a>' % esc(num)
        if vd:
            vc = {"真实": "v-true", "部分真实": "v-part"}.get(vd, "v-none")
            frag += '<span class="v %s">%s</span>' % (vc, esc(vd))
        items.append(frag)
    return "".join(items)


def _actors_rail_html(spec, verdicts=None):
    """主体轨道网格（v0.2.18）：身份简介卡组 + 主体×离散阶段轨道矩阵 + 跨主体交锋带。

    spec = {"title","subtitle","topology","span",
            "stages":[{"name","span","hot"}],
            "actors":[{"name","role","layer":"main|minor","bio","side","type","position","stance",
                       "nodes":[{"col",|"stage","date","text","kind"}],"claims","ref","credibility"}],
            "cross":[{"from","rel","to","tag"}]}
    注入位置：第二章（事件时间线）之后，作为 h3 小节（CSS 计数器自动编号 x.1）。
    列 = 离散阶段（非时间比例）→ 主体数/稀疏度/跨度/拓扑变化均不崩。
    """
    actors = [a for a in (spec.get("actors") or []) if isinstance(a, dict)]
    if not actors:
        return ""
    stages = [s for s in (spec.get("stages") or [])
              if isinstance(s, dict) and str(s.get("name", "")).strip()]
    if not stages:
        cols = []
        for a in actors:
            for nd in (a.get("nodes") or []):
                if isinstance(nd, dict) and nd.get("col") is not None:
                    try:
                        cols.append(int(nd.get("col")))
                    except (TypeError, ValueError):
                        pass
        stages = [{"name": "阶段 %d" % i, "span": ""} for i in range(1, (max(cols) if cols else 0) + 1)]
    ncol = len(stages)
    if not ncol:
        return ""
    # 越界/未匹配阶段名的节点：显式提示，不静默丢弃（承「不静默假成功」原则）
    for _a in actors:
        for _nd in (_a.get("nodes") or []):
            if not isinstance(_nd, dict):
                continue
            _c = _node_col(_nd, stages)
            if _c is None or _c < 1 or _c > ncol:
                print("[report] 警告：主体「%s」的节点「%s %s」列号越界或未匹配阶段，已忽略（stages 共 %d 列）"
                      % (_a.get("name", ""), _nd.get("date", ""), _nd.get("text", ""), ncol))

    def is_main(a):
        return str(a.get("layer", "") or "").strip().lower() in ("main", "主轴", "primary", "1", "true")

    def role_of(a):
        return str(a.get("role", "") or a.get("type", "") or "").strip()

    meta = ["主体 %d" % len(actors), "阶段 %d" % ncol]
    span = str(spec.get("span", "") or "").strip()
    if not span:
        sp = [str(s.get("span", "") or "").strip() for s in stages]
        sp = [x for x in sp if x]
        if sp:
            span = "%s ~ %s" % (sp[0].split("~")[0].strip(), sp[-1].split("~")[-1].strip())
    if span:
        meta.append("跨度 %s" % span)
    topo = str(spec.get("topology", "") or "").strip()
    if topo:
        meta.append("拓扑：%s" % topo)

    out = ['<h3>%s</h3>' % esc(str(spec.get("title", "") or "主体视角档案：各方行动链与主张"))]
    out.append('<p class="chapter-intro">按事件主体重投影第二章时间线：行动链为该方节点的日期压缩；'
               '主张编号指向第三章断言、徽章语义同第四章——不新增任何事实。</p>')
    out.append('<div class="rail-card">')
    subtitle = str(spec.get("subtitle", "") or "").strip()
    head = '<div class="rail-head">'
    if subtitle:
        head += '<span class="rail-title">%s</span>' % esc(subtitle)
    head += '<span class="rail-meta">%s</span></div>' % esc(" · ".join(meta))
    out.append(head)

    # 身份简介卡组（编号 A1..An 与轨道行 .aid 一一对应）
    out.append('<div class="rail-actors">')
    for i, a in enumerate(actors, 1):
        main = is_main(a)
        role = role_of(a)
        role_line = " · ".join(x for x in (role, "主轴" if main else "次要") if x)
        bio = str(a.get("bio", "") or a.get("position", "") or a.get("stance", "") or "").strip()
        card = ['<div class="racard%s">' % (" main" if main else "")]
        card.append('<div class="racard-h"><span class="racard-id">A%d</span>'
                    '<span class="racard-n">%s</span></div>' % (i, esc(str(a.get("name", "") or ""))))
        if role_line:
            card.append('<span class="racard-r">%s</span>' % esc(role_line))
        if bio:
            card.append('<p class="racard-b">%s</p>' % esc(bio))
        cl = _claims_html(a.get("claims"), verdicts)
        if cl:
            card.append('<p class="racard-claims">主张 %s</p>' % cl)
        card.append('</div>')
        out.append("".join(card))
    out.append('</div>')

    # 轨道矩阵：行 = 主体，列 = 离散阶段
    out.append('<table class="rail">')
    th = ['<thead><tr><th class="rail-actor">主体 · 角色</th>']
    for s in stages:
        sp_txt = str(s.get("span", "") or "").strip()
        th.append('<th>%s%s</th>' % (esc(str(s.get("name", "") or "")),
                                     '<span class="rg">%s</span>' % esc(sp_txt) if sp_txt else ''))
    th.append('</tr></thead>')
    out.append("".join(th))
    out.append('<tbody>')
    for i, a in enumerate(actors, 1):
        main = is_main(a)
        role = role_of(a)
        row = ['<tr%s>' % (' class="main"' if main else '')]
        row.append('<td class="rail-actor"><span class="aid">A%d</span>%s%s</td>'
                   % (i, esc(str(a.get("name", "") or "")),
                      '<span class="sub">%s</span>' % esc(role) if role else ''))
        for c in range(1, ncol + 1):
            cells = []
            for nd in (a.get("nodes") or []):
                if isinstance(nd, dict) and _node_col(nd, stages) == c:
                    frag = _rn_html(nd)
                    if frag:
                        cells.append(frag)
            cls = "cell hot" if (stages[c - 1].get("hot") and cells) else "cell"
            row.append('<td class="%s">%s</td>' % (cls, "".join(cells)))
        row.append('</tr>')
        out.append("".join(row))
    out.append('</tbody></table>')

    # 交锋带：跨主体关系降维为序列（不画连线）
    cbits = []
    for c in (spec.get("cross") or []):
        if not isinstance(c, dict):
            continue
        frm = str(c.get("from", "") or "").strip()
        rel = str(c.get("rel", "") or "").strip()
        to = str(c.get("to", "") or "").strip()
        tag = str(c.get("tag", "") or "").strip()
        if frm:
            cbits.append("<span>%s</span>" % esc(frm))
        if rel:
            cbits.append('<span class="rel">%s →</span>' % esc(rel))
        if to:
            cbits.append("<span>%s</span>" % esc(to))
        if tag:
            cbits.append("<span>（%s）</span>" % esc(tag))
    if cbits:
        out.append('<div class="rail-cross"><b>交锋带</b>%s</div>' % "".join(cbits))
    out.append('</div>')
    out.append('<p class="rn-legend"><span>● 官方行动</span><span>▲ 声明 / 发布</span>'
               '<span>○ 表态 · 被指行为</span><span>◈ 裁决 / 评审</span><span>┄ 静默 · 未见表态</span>'
               '<span>｜ 左侧色条 = 主轴主体</span></p>')
    return "\n".join(out)


def _actors_html(actors):
    """--actors 分流（v0.2.18）：list → 旧「角色小词典」（挂第一章末）；
    dict → 「主体轨道网格」（挂第二章末）。无输入返回空串（不渲染）。"""
    if isinstance(actors, dict):
        return _actors_rail_html(actors)
    return _actors_lexicon_html(actors or [])


def _actors_lexicon_html(actors):
    """角色小词典（v0.2.6 旧行为）：第一章末可选折叠卡组（按 side 分组）。"""
    groups, order = {}, []
    for a in actors or []:
        if not isinstance(a, dict):
            continue
        side = str(a.get("side", "") or "未标注")
        if side not in groups:
            groups[side] = []
            order.append(side)
        groups[side].append(a)
    total = sum(len(v) for v in groups.values())
    if not total:
        return ""
    out = ['<details class="fold"><summary>角色小词典 · %d 个（点击展开/收起）</summary>' % total]
    for side in order:
        out.append('<p class="actors-side">— %s —</p>' % esc(side))
        out.append('<div class="grid">')
        for a in groups[side]:
            cred = _mark_html(a.get("credibility", ""))
            name = esc(a.get("name", "未命名"))
            meta_bits = " · ".join(x for x in (esc(a.get("type", "")), esc(a.get("position", ""))) if x)
            card = ['<article class="card"><h3>%s %s</h3>' % (name, cred)]
            if meta_bits:
                card.append("<p>%s</p>" % meta_bits)
            stance = esc(a.get("stance", ""))
            if stance:
                card.append("<p><strong>立场：</strong>%s</p>" % stance)
            quotes = str(a.get("quotes", "") or "")
            if quotes.strip():
                card.append('<p class="quotes"><strong>关键言论：</strong>%s</p>'
                            % "<br>".join(esc(q.strip()) for q in quotes.split("\n") if q.strip()))
            ref = str(a.get("ref", "") or "")
            if ref.strip():
                card.append('<div class="meta">信源 %s</div>' % _refs_html(ref))
            card.append("</article>")
            out.append("".join(card))
        out.append("</div>")
    out.append("</details>")
    return "\n".join(out)


TRACK_TYPE = {
    "statement": (None, None),
    "official": ("▲官方", "mark-official"),
    "confirm": ("●证实", "mark-multi"),
    "partial": ("◐口径差异", "mark-single"),
    "refute": ("✗证伪", "mark-caution"),
    "open": ("?待核实", "mark-single"),
}


def _track_badge(t):
    label, cls = TRACK_TYPE.get(str(t or "").strip().lower(), (None, None))
    if not label:
        return ""
    return '<span class="mark %s">%s</span> ' % (cls, esc(label))


def _track_html(track):
    """说法×证实双轨时间轴：第二章末可选；rows=[{day,lane,type,text,ref}]。"""
    rows = (track or {}).get("rows") or []
    items = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        text = str(r.get("text", "")).strip()
        if not text:
            continue
        day = str(r.get("day", "")).strip() or "（日期未知）"
        lane = str(r.get("lane", "claim")).strip().lower()
        lane = lane if lane in ("claim", "verify") else "claim"
        typ = str(r.get("type", "statement")).strip().lower()
        items.append((day, lane, typ, text, str(r.get("ref", "")).strip()))
    if not items:
        return ""
    lane_rank = {"claim": 0, "verify": 1}
    items.sort(key=lambda x: (x[0] == "（日期未知）", x[0], lane_rank[x[1]]))
    out = ["<h3>说法 × 证实 双轨时间轴（%d 条）</h3>" % len(items),
           '<table class="dualtrack"><thead><tr><th>日期</th><th>说法 / 舆论</th><th>证实 / 证伪</th></tr></thead><tbody>']
    prev_day = None
    for day, lane, typ, text, ref in items:
        day_cell = esc(day) if day != prev_day else ""
        prev_day = day
        txt = esc(text) + (" " + _refs_html(ref) if ref else "")
        badge = _track_badge(typ)
        if lane == "claim":
            out.append('<tr><td>%s</td><td class="cell-claim">%s</td><td class="cell-verify"></td></tr>'
                       % (day_cell, txt))
        else:
            out.append('<tr><td>%s</td><td class="cell-claim"></td><td class="cell-verify">%s%s</td></tr>'
                       % (day_cell, badge, txt))
    out.append("</tbody></table>")
    out.append('<p class="legend">徽章：▲官方 ｜ ●证实 ｜ ◐口径差异/部分证实 ｜ ✗证伪 ｜ ?待核实。同日并排 = 说法出现与证实/证伪的时间对应。</p>')
    return "\n".join(out)


def _masthead_html(event, facts, opinion, recs, title, intro, data_meta=None):
    """报头区（模板 .masthead/.hero-grid，自动数字 hero-figure）。
    title 可含 <em>（不转义，信任 LLM/用户输入）；intro 按普通文本转义。"""
    c = ['<div class="masthead">', '<div class="hero-grid">', "<div>"]
    c.append('<div class="eyebrow">社会事件综合汇编 · 采集 → 事实还原 → 舆论把握 → 报告</div>')
    c.append("<h1>%s</h1>" % (title or esc(event)))
    if intro:
        c.append('<p class="intro">%s</p>' % esc(intro))
    c.append("</div>")

    items = []
    start, end, raw_total, n_recs, dropped = _event_span(recs, data_meta)
    claims = (facts or {}).get("claims") or []
    dist = (opinion or {}).get("stance_distribution") or []
    if n_recs:
        plat = _platform_counts(recs)
        plat_s = "、".join("%s%d" % (PLATFORM_NAMES.get(p, p)[:3], n) for p, n in plat.items())
        sub = "分析语料 · " + (plat_s[:40] or "")
        if dropped:
            sub += "（原始 %d，剔除 %d）" % (raw_total, dropped)
        items.append(("%d" % n_recs, "条", sub))
    if claims:
        cnt = {}
        for cl in claims:
            v = cl.get("verdict", "待核查")
            cnt[v] = cnt.get(v, 0) + 1
        verdict_s = " / ".join("%s%d" % ({k: {"真实": "真实", "部分真实": "部分", "失实": "失实", "证据不足": "不足"}.get(k, k)}[k], v) for k, v in cnt.items())
        items.append(("%d" % len(claims), "条", "事实断言 · " + verdict_s))
    if dist:
        top = dist[0]["stance"]
        cov = (opinion or {}).get("coverage_rate")
        sub = "立场聚类 · " + top[:22]
        if cov:
            sub += "（覆盖率%.1f%%）" % float(cov)
        items.append(("%d" % len(dist), "个", sub))
    if items:
        f = ['<figure class="hero-figure" aria-label="报告关键数字">']
        for num, unit, sub in items[:3]:
            f.append('<p class="kpi">%s<span>%s</span></p>' % (num, unit))
            f.append('<p class="kpi-sub">%s</p>' % esc(sub))
        f.append('<ul><li><span class="mark mark-official">▲官方</span>'
                 '<span class="mark mark-multi">●多源</span>'
                 '<span class="mark mark-single">△单源</span></li></ul>')
        f.append("</figure>")
        c.append("".join(f))
    c.append("</div>")
    c.append("</div>")
    return "\n".join(c)


def build_content(event, facts, opinion, sources, recs, viewpoint_md, coverage_md, extra_md, intros=None,
                  actors=None, track=None, mast_title="", mast_intro="", abstract_text="", nav_drawer=True,
                  milestones=None, compliance=None, stance_cards=None, data_meta=None):
    viewpoint_md = _read_md(viewpoint_md)
    coverage_md = _read_md(coverage_md)
    intros = intros or {}
    actors_lex = actors if isinstance(actors, list) else []
    actors_rail = actors if isinstance(actors, dict) else {}
    # 裁决词以登记册为唯一来源（CONTRACT-joint §9）：claims 只带编号，徽章由 facts 补齐
    verdicts = {}
    for _i, _c in enumerate(((facts or {}).get("claims") or []), 1):
        if isinstance(_c, dict):
            verdicts[_i] = _c.get("verdict", "")
    actors_html = _actors_lexicon_html(actors_lex) if actors_lex else ""
    rail_html = _actors_rail_html(actors_rail, verdicts) if actors_rail else ""
    track_html = _track_html(track or {})
    # 来源索引先算一次（v0.3）：ch5 高赞代表需要 url→refN 映射，而索引渲染在 ch5 之后
    srcs = _collect_sources(sources, recs, _cited_sources(opinion))
    url_ref = {str(s.get("url", "")).strip(): i for i, s in enumerate(srcs, 1)
               if str(s.get("url", "")).strip()}
    secs = [
        sec_overview(event, facts, opinion, recs, abstract_text, data_meta),
        sec_timeline(opinion, recs, milestones, data_meta),
        sec_facts(facts, extra_md),
        sec_check(facts),
        sec_opinion_analysis(facts),
        sec_opinion(opinion, viewpoint_md, stance_cards, url_ref),
        sec_sources(srcs),
        sec_coverage(facts, recs, coverage_md, compliance),
        sec_method(),
        sec_timestamp(recs),
    ]
    titles = ["结论速览", "事件时间线", "核心事实汇编", "事实核查结果", "观点辨析与推理说明",
              "舆论观点综合", "来源索引", "覆盖完整性声明", "方法论", "信息与生成时间戳"]
    cn = "一二三四五六七八九十"
    # --intros 按章节 id 取；旧文件的数字键经 LEGACY_INTRO_IDX 映射（插章不再错位）
    intros_by_id = {}
    for _k, _v in (intros or {}).items():
        _ks = str(_k)
        intros_by_id[LEGACY_INTRO_IDX.get(_ks, _ks)] = _v
    defaults = _default_intros(facts, opinion, sources, recs)
    toc = ['<nav class="toc" aria-label="目录">'] if not nav_drawer else []
    anchored = []
    for i, (sec, title) in enumerate(zip(secs, titles), 1):
        sid = CH_IDS[i - 1]
        cid = "ch%d" % i
        if not nav_drawer:
            toc.append('<a href="#%s">%s、%s</a>' % (cid, cn[i - 1], title))
        intro = intros_by_id.get(sid) or defaults.get(sid)
        if intro:
            sec = sec.replace("</h2>", "</h2>\n<p class=\"chapter-intro\">%s</p>" % esc(intro), 1)
        # 可选组件：角色词典（list）挂第一章末；主体轨道网格（dict）与双轨轴挂第二章末
        if i == 1 and actors_html:
            sec += "\n" + actors_html
        if i == 2:
            if rail_html:
                sec += "\n" + rail_html
            if track_html:
                sec += "\n" + track_html
        # 锚点置于 h2 之前（不改写 h2 文本，兼容既有结构/测试）
        anchored.append('<a id="%s" class="sec-anchor"></a>%s' % (cid, sec))
    if extra_md and os.path.exists(extra_md):
        with open(extra_md, "r", encoding="utf-8-sig") as f:
            app = '<a id="ch-appendix" class="sec-anchor"></a><h2>附录：补充说明</h2>'
            a_intro = intros.get("appendix")
            if a_intro:
                app += '<p class="chapter-intro">%s</p>' % esc(a_intro)
            app_md = re.sub(r"(?m)^##", "#", f.read())  # 附录小节整体上调一级：##→#（h4→h3），避免 h2→h4 跳级（x.0.y）
            anchored.append(app + md_to_html(app_md))
        if not nav_drawer:
            toc.append('<a href="#ch-appendix">附录：补充说明</a>')
    if toc:
        toc.append('</nav>')
    mast = _masthead_html(event, facts, opinion, recs, mast_title, mast_intro, data_meta)
    parts = [mast] + (["".join(toc)] if toc else []) + anchored
    parts.append('<p class="footer">由 bai-bao-dai（百宝袋）生成 · 仅供学习研究</p>')
    return "\n".join(parts)


def load_relevant(normalized_path):
    """优先读治理后的语料，并给出「原始 → 可用 → 剔除」的口径（v0.4）。

    约定（CONTRACT-joint §10 / 百宝袋 SKILL.md 阶段 2）：语料治理输出固定文件名
      normalized/data.relevant.jsonl（留下的）与 normalized/excluded.jsonl（剔除的），
      而 normalized/data.jsonl 是治理前的全量。
    传 data.relevant.jsonl 也成立：原始量从同目录 data.jsonl 取，不被"拿过滤后当原始"绕过。
    找不到 data.relevant.jsonl 时退化为"未治理"并打印提示——不静默假装治理过。

    返回 (recs, data_meta)；data_meta = {raw_total, relevant_total, dropped, start, end}。
    """
    p = os.path.abspath(normalized_path) if normalized_path else ""
    d = os.path.dirname(p) if p else ""
    rel = os.path.join(d, "data.relevant.jsonl") if d else ""
    exc = os.path.join(d, "excluded.jsonl") if d else ""
    plain = os.path.join(d, "data.jsonl") if d else ""

    def _count(path):
        return len(load_normalized(path)) if (path and os.path.exists(path)) else None

    src = p
    raw_total = None
    if rel and os.path.exists(rel):
        src = rel
        raw_total = _count(plain)
    elif p and os.path.basename(p) != "data.jsonl" and plain and os.path.exists(plain):
        src = p
        raw_total = _count(plain)
    elif p and os.path.exists(p):
        print("[report] 提示：未找到 %s（语料治理产物）——本次按未治理语料装配，"
              "「原始→可用」口径无法给出。请先跑 relevance_gate.py（见 SKILL.md 阶段 2）"
              % os.path.basename(rel or "data.relevant.jsonl"))
    recs = load_normalized(src)
    n_exc = _count(exc)
    if raw_total is None:
        raw_total = len(recs)
    if n_exc is None:
        n_exc = max(0, raw_total - len(recs))
    meta = {"raw_total": raw_total, "relevant_total": len(recs), "dropped": n_exc,
            "start": "", "end": ""}
    print("[report] 语料口径：原始 %d 条 → 分析 %d 条（剔除 %d 条）"
          % (meta["raw_total"], meta["relevant_total"], meta["dropped"]))
    return recs, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True)
    ap.add_argument("--facts", default="")
    ap.add_argument("--opinion", default="")
    ap.add_argument("--normalized", default="", help="normalized data.jsonl（时间线/覆盖/来源自动统计）")
    ap.add_argument("--sources", default="", help="来源索引 json（[{name,url}]）")
    ap.add_argument("--viewpoint", default="", help="LLM 视点综合 markdown")
    ap.add_argument("--coverage", default="", help="LLM 覆盖完整性补充 markdown")
    ap.add_argument("--extra", default="", help="附加 markdown（附录）")
    ap.add_argument("--actors", default="", help="主体/角色 json（可选，无则不渲染）：① 数组=角色小词典（挂第一章末）[ {name,type,side,position,stance,quotes,ref,credibility} ]；② 对象=主体轨道网格（挂第二章末，v0.2.18）{stages:[{name,span,hot}], actors:[{name,role,layer:main|minor,bio,nodes:[{col|stage,date,text,kind:act|decl|view|judge|quiet}],claims}], cross:[{from,rel,to,tag}]}")
    ap.add_argument("--stance-cards", default="", help="立场卡组 json（v0.2.13）：[{name,pct,view,steps,quotes[{like,txt}],note}]（可选；缺省按 opinion 立场程序化回退生成基础卡组）")
    ap.add_argument("--track", default="", help="说法×证实双轨轴 json：{\"rows\":[{day,lane:claim|verify,type,text,ref}]}（可选，无则不渲染）")
    ap.add_argument("--intros", default="", help="章节导读覆盖 json：按章节 id 取 {overview|timeline|facts|check|analysis|opinion|sources|coverage|method|timestamp|appendix}；"
                                                 "旧文件的数字键 \"1\"..\"9\" 仍向后兼容（经 LEGACY_INTRO_IDX 映射）。缺省按数据程序化生成导航句")
    ap.add_argument("--title", default="", help="报头主标题（可含 <em> 强调）；缺省用 --event 原文")
    ap.add_argument("--intro", default="", help="报头一句话导语（可选）；留空则不渲染 intro")
    ap.add_argument("--template", default="", help="模板 HTML 路径（默认：技能自带副本 templates/report_template.html；试验/副本验证用）")
    ap.add_argument("--abstract", default="", help="事件摘要 markdown（自动识别：结构化标签式——标签单独成段写 **一句话结论** 等、后空行接内容；或旧两段式——空行分段，段一事件→进展→舆论、段二真实性→结构提示）")
    ap.add_argument("--timeline-milestones", default="", help="竖式里程碑时间轴 json：[{date,type,tag,title,text,count}]（可选；缺省回退日期×记录数表）")
    ap.add_argument("--nav-drawer", dest="nav_drawer", action=argparse.BooleanOptionalAction, default=True, help="右侧毛玻璃悬浮目录（默认开；--no-nav-drawer 关闭）")
    ap.add_argument("--joint", action="store_true", help="三技能联合任务模式：注入联合 meta 标记，且必须提供 --compliance 合规账本（CONTRACT-joint）")
    ap.add_argument("--compliance", default="", help="采集合规账本 json（联合任务必需）：[{platform,status,block,escalated,user_confirmed,alternative,note}]")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    facts = load_json(args.facts)
    opinion = load_json(args.opinion)
    sources = load_json(args.sources)
    recs, data_meta = load_relevant(args.normalized)
    intros = load_json(args.intros)
    if not isinstance(intros, dict):
        intros = {}
    actors = load_json(args.actors)
    if isinstance(actors, dict):
        if actors.get("actors"):
            print("[report] 主体轨道网格：%d 主体 × %d 阶段 → 挂第二章末（h3 自动编号 x.1）"
                  % (len([a for a in actors["actors"] if isinstance(a, dict)]),
                     len(actors.get("stages") or [])))
        else:
            actors = {}
    elif not isinstance(actors, list):
        actors = []
    track = load_json(args.track)
    if not isinstance(track, dict):
        track = {}
    stance_cards = load_json(args.stance_cards)
    if not isinstance(stance_cards, list):
        stance_cards = None
    milestones = load_json(args.timeline_milestones)
    if not isinstance(milestones, list):
        milestones = []
    # 事件跨度以时间线节点为准（v0.4）：语料里的背景/历史坐标不进 KPI
    year_hint = ""
    for r in recs:
        y = str(r.get("time", ""))[:4]
        if y.isdigit():
            year_hint = y
            break
    sp_start, sp_end = _span_from_milestones(milestones, year_hint)
    if sp_start and sp_end:
        data_meta["start"], data_meta["end"] = sp_start, sp_end
        print("[report] 事件跨度（取自时间线）：%s ~ %s" % (sp_start, sp_end))
    compliance = load_json(args.compliance) if args.joint else None
    if args.joint and not compliance:
        print("[report] 错误：--joint 模式必须提供合规账本 --compliance（schema 见 CONTRACT-joint §4）")
        return 1

    tpl_path = args.template or TEMPLATE
    if not os.path.exists(tpl_path):
        print("[report] 错误：模板文件不存在：%s（可用 --template 指定，或检查技能自带副本 templates/report_template.html）" % tpl_path)
        return 1
    with open(tpl_path, "r", encoding="utf-8") as f:
        html = f.read()

    abstract_text = _read_md(args.abstract)
    content = build_content(args.event, facts, opinion, sources, recs,
                            args.viewpoint, args.coverage, args.extra, intros,
                            actors, track, args.title, args.intro,
                            abstract_text=abstract_text, nav_drawer=args.nav_drawer,
                            milestones=milestones, compliance=compliance,
                            stance_cards=stance_cards, data_meta=data_meta)
    html = html.replace("<!-- TITLE -->", esc(args.event) + " · 综合分析报告")
    # 单对标记装配：替换模板 CONTENT_START~CONTENT_END 之间的占位注释段，避免残留第二个 END
    html = re.sub(r"<!-- CONTENT_START -->.*?<!-- CONTENT_END -->",
                  "<!-- CONTENT_START -->\n" + content + "\n<!-- CONTENT_END -->",
                  html, flags=re.S)
    if args.nav_drawer:
        html = html.replace("</body>", _bnav_html() + "\n</body>")
    # v0.2.13：折叠渐缓弹出 + 引用悬停气泡（无目标时安全空转）
    html = html.replace("</body>", _fold_js() + _refpop_js() + "\n</body>")
    if args.joint:
        html = html.replace("</head>", '<meta name="dsh-report" content="joint;contract=joint-v0.5">\n</head>', 1)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print("[report] 已生成 → %s" % args.out)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
