# -*- coding: utf-8 -*-
"""bai-bao-dai 综合报告生成（9 章结构）。

读 facts.json / opinion.json / normalized jsonl / sources.json，
用 templates/report_template.html 渲染 report.html。章节：
  一、结论速览      二、事件时间线      三、核心事实汇编
  四、事实核查结果  五、舆论观点综合    六、来源索引
  七、覆盖完整性声明 八、方法论          九、信息与生成时间戳

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

EV_LABELS = {1: "官方文件/本人账号原文", 2: "权威媒体全文", 3: "转载引文", 4: "搜索摘要/标题层"}


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
    """行内 markdown：加粗/斜体/链接/行内代码。text 需已 esc()。"""
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
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
    """事件摘要两段式（空行分段）：段一 事件→进展→舆论；段二 真实性→结构提示。"""
    t = (text or "").strip()
    if not t:
        return ""
    paras = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    if not paras:
        return ""
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
def _kpi(num_html, lab, sub):
    """仪表盘 KPI 卡：num_html 为大数字（可含 <span> 单位），lab 标签，sub 说明。"""
    return ('<div class="kpi-card"><div class="kpi-num">%s</div>'
            '<div class="kpi-lab">%s</div><div class="kpi-sub">%s</div></div>'
            % (num_html, esc(lab), esc(sub) if sub else ""))


def sec_overview(event, facts, opinion, recs, abstract_text=""):
    c = ["<h2>一、结论速览</h2>"]
    _abs = _abstract_html(abstract_text)
    if _abs:
        c.append(_abs)
    c.append('<div class="kpis" aria-label="报告仪表盘">')
    total = len(recs)
    plat = _platform_counts(recs)
    sub1 = "、".join("%s %d" % (PLATFORM_NAMES.get(p, p), n) for p, n in plat.items()) or "无采集记录"
    c.append(_kpi("%d<span>条</span>" % total, "采集量", sub1))
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
        sub3 = "主立场：%s（%.1f%%）" % (top["stance"], top["pct"])
        minority = [d["stance"] for d in dist if d.get("minority")]
        if minority:
            sub3 += "；少数派：" + "、".join(minority)
        c.append(_kpi("%d<span>个</span>" % len(dist), "舆论立场", sub3))
    else:
        c.append(_kpi("—", "舆论立场", "未提供舆论数据（opinion.json）"))
    ts = []
    if opinion and opinion.get("timeline"):
        ts = sorted(opinion["timeline"].keys())
    else:
        ts = sorted({str(r.get("time", ""))[:10] for r in recs if r.get("time")})
    if len(ts) >= 2:
        span = "%s ~ %s" % (ts[0], ts[-1])
        try:
            days = (date.fromisoformat(ts[-1]) - date.fromisoformat(ts[0])).days + 1
            c.append(_kpi("%d<span>天</span>" % days, "时间跨度", span))
        except Exception:
            c.append(_kpi("—", "时间跨度", span))
    elif len(ts) == 1:
        c.append(_kpi("1<span>天</span>", "时间跨度", ts[0]))
    else:
        c.append(_kpi("—", "时间跨度", "无时间数据"))
    c.append("</div>")
    c.append('<p class="legend">确定性标记：▲官方确认 / ●多源一致 / △单源存疑。数值由脚本自动汇总，深度结论见后续章节。</p>')
    return "\n".join(c)


def _platform_counts(recs):
    out = {}
    for r in recs:
        out[r.get("platform", "unknown")] = out.get(r.get("platform", "unknown"), 0) + 1
    return out


def _default_intros(facts, opinion, sources, recs):
    """程序化默认章节导读（导航句）。无数据的章节不生成（章内自带空提示）；ch1 以仪表盘为导读，不生成。"""
    out = {}
    tl = []
    if opinion and opinion.get("timeline"):
        tl = sorted(opinion["timeline"].keys())
    elif recs:
        tl = sorted({str(r.get("time", ""))[:10] for r in recs if r.get("time")})
    if len(tl) >= 2:
        out["2"] = "时间线共 %d 个记录日（%s ~ %s），按日聚合采集记录数。" % (len(tl), tl[0], tl[-1])
    elif tl:
        out["2"] = "时间线记录日：%s（本事件仅 1 个记录日）。" % tl[0]
    if facts and facts.get("claims"):
        n = len(facts["claims"])
        out["3"] = "本章汇编 %d 条事实断言及其原始表述/出处；逐条裁决见第四章。" % n
        cnt = {v: 0 for v in VERDICT_ORDER}
        for cl in facts["claims"]:
            v = cl.get("verdict", "待核查")
            cnt[v] = cnt.get(v, 0) + 1
        s = "；".join("%s %d" % (v, c) for v, c in cnt.items() if c)
        out["4"] = "LLM 已对 %d 条断言裁决：%s；证据等级与裁决词见各章标注。" % (n, s)
    if opinion and opinion.get("stance_distribution"):
        dist = opinion["stance_distribution"]
        top = dist[0]
        minority = [d["stance"] for d in dist if d.get("minority")]
        m = "；少数派：" + "、".join(minority) if minority else ""
        out["5"] = "舆论按 %d 个立场聚类：主立场「%s」（%.1f%%）%s；视点综合见本章后半。" % (len(dist), top["stance"], top["pct"], m)
    srcs = _collect_sources(sources, recs)
    if srcs:
        n_cur = len(sources or [])
        extra = len(srcs) - n_cur
        if extra > 0:
            out["6"] = "来源索引共 %d 条：前 %d 条为精选信源，其后 %d 条为平台语料样本链接（供逐帖回溯；转载簇去重见第七章）；正文证据以第三章「证据源」列表呈现。A=官方/权威，B=主流，C=自媒体/样本。" % (len(srcs), n_cur, extra)
        else:
            out["6"] = "来源索引共 %d 条；A=官方/权威，B=主流，C=自媒体。" % len(srcs)
    plat = _platform_counts(recs)
    plat_s = "、".join("%s %d" % (PLATFORM_NAMES.get(p, p), n) for p, n in plat.items()) or "无采集"
    out["7"] = "平台覆盖：%s；未覆盖平台/未回应方与信源缺口见本章表格与补充说明。" % plat_s
    out["8"] = "本章说明报告生成方法（采集→事实还原→舆论把握→综合）与确定性标记规则。"
    out["9"] = "本章记录报告生成时刻与信息截止时间；搜索索引存在延迟，结论可能滞后。"
    return out


def _timeline_html(milestones):
    """竖式里程碑时间轴（.tl）：milestones=[{date,type,tag,title,text,count}]。
    type ∈ official/media/view/bg/quiet/check；count 缺省不显示采集量。"""
    items = []
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
        cnt_html = ('<span class="tl-cnt">采集 %s 条</span>' % esc(cnt)) if cnt else ""
        items.append(
            '<div class="tl-item"><div class="tl-date"><b>%s</b>%s</div>'
            '<span class="tl-dot tg-%s"></span>'
            '<div class="tl-body"><span class="tl-tag tg-%s">%s</span>'
            '<strong>%s</strong><p>%s</p></div></div>'
            % (esc(date), cnt_html, esc(t), esc(t), esc(tag), esc(title), esc(text)))
    if not items:
        return ""
    return ('<div class="tl" aria-label="事件时间线">\n' + "\n".join(items) + "\n</div>\n"
            '<p class="legend">节点类型：绿=官方 · 蓝=媒体 · 陶土=解读/舆论 · 金=背景 · 灰=收尾 · 紫=核查。日计数为当日平台采集记录数。</p>')


def sec_timeline(opinion, recs, milestones=None):
    c = ["<h2>二、事件时间线</h2>"]
    tl_html = _timeline_html(milestones)
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
            c.append('<details class="fold"><summary>说明与证据源</summary>'
                     '<p>%s</p><p class="caption">证据源：%s</p>%s</details>'
                     % (esc(cl.get("reason", "")), esc(ev), ev_note))
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
             '<dt>裁决依据</dt><dd>china_sources 初查 ＋ LLM 多源交叉（详见第八章）</dd></dl></div>')

    def _cat(cl):
        v = str(cl.get("verdict", ""))
        if "观点" in v:
            return ("opinion", "真实（观点性判断）")
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
            "true": "v-true", "opinion": "v-part", "part": "v-part",
            "false": "v-false", "none": "v-none"}[key]})
        groups[key]["ids"].append("#%d" % i)
    pills = []
    for key in ("true", "opinion", "part", "false", "none"):
        g = groups.get(key)
        if g:
            pills.append('<span class="vpill %s">%s <span class="n">%d</span></span>'
                         % (g["cls"], esc(g["label"]), len(g["ids"])))
    c.append('<div class="vpills">' + "".join(pills) + "</div>")
    c.append("<h3>按结论分组速览</h3>")
    descr = {
        "true": "—— 证据多源一致支持；其中证据≤转载层者徽章显示为「报道属实（原文未核）」（语义仍为真实，见第三章折叠说明）。",
        "opinion": "—— 律师/专家等观点性判断，系个人解读口径而非官方结论。",
        "part": "—— 要素部分可证、部分存出入或未逐一核验（口径冲突见下表）。",
        "false": "—— 与可靠信源相悖或已被否定。",
        "none": "—— 无具名信源/检索未见/无法核验（含否定性主张的默认裁决）；不等于确认不存在或确认失实。",
    }
    for key in ("true", "opinion", "part", "false", "none"):
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
            if cf.get("a") or cf.get("b"):
                a = (cf.get("a") or {}); b = (cf.get("b") or {})
                cf_rows.append((esc(a.get("claim", "")) + _cap(a.get("src", "")),
                                esc(b.get("claim", "")) + _cap(b.get("src", "")),
                                esc(cl.get("verdict", "")), esc(cf.get("note", "")), "#%d" % i))
            elif cf.get("issue"):
                cf_rows.append((esc(cf.get("issue", "")), "", esc(cf.get("verdict", "")),
                                esc(cf.get("note", "")), "#%d" % i))
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


def sec_opinion(opinion, viewpoint_md):
    c = ["<h2>五、舆论观点综合</h2>"]
    if opinion and opinion.get("stance_distribution"):
        dist = opinion["stance_distribution"]
        # 立场占比条形图（纯 CSS；保留下方表格便于精确阅读/打印）
        c.append('<div class="bars" aria-label="立场占比条形图">')
        for d in dist:
            pct = d.get("pct", 0) or 0
            c.append('<div class="bar-row"><span class="bar-label">%s</span>'
                     '<span class="bar-track"><span class="bar-fill" style="width:%s%%"></span></span>'
                     '<span class="bar-val">%s%% · %s 条</span>%s</div>'
                     % (esc(d.get("stance", "")), pct, pct, d.get("count", 0),
                        '<span class="mark mark-single">少数派</span>' if d.get("minority") else ""))
        c.append("</div>")
        c.append("<table><thead><tr><th>立场</th><th>占比</th><th>条数</th><th>少数派</th><th>高赞代表（前3）</th></tr></thead><tbody>")
        for d in dist:
            tops = "；".join("[赞%s] %s" % (s["likes"], _clip_text(s["content"]))
                             for s in d.get("top_samples", []))
            c.append("<tr><td>%s</td><td>%s%%</td><td>%d</td><td>%s</td><td>%s</td></tr>" % (
                esc(d["stance"]), d["pct"], d["count"],
                "是" if d.get("minority") else "—", tops))
        c.append("</tbody></table>")
        # 焦点转移：立场 × 日期 堆叠条（opinion.stance_timeline 为新增字段，缺失则回退原提示）
        st = opinion.get("stance_timeline")
        if isinstance(st, dict) and st:
            order = [d["stance"] for d in dist]
            c.append('<div class="stacks" aria-label="焦点转移（立场×日期）">')
            for day in sorted(st):
                segs = []
                for i, sname in enumerate(order, 1):
                    n = (st[day] or {}).get(sname, 0)
                    if n:
                        segs.append('<span class="stack-seg s%d" style="flex:%d"></span>' % (i, n))
                c.append('<div class="stack-row"><span class="stack-date">%s</span>'
                         '<span class="stack-track">%s</span></div>' % (esc(day), "".join(segs)))
            c.append('<div class="stack-legend">')
            for i, sname in enumerate(order, 1):
                c.append('<span><i class="s%d"></i>%s</span>' % (i, esc(sname)))
            c.append("</div></div>")
        elif opinion.get("timeline"):
            c.append("<p>焦点时间分布见第二章时间线。</p>")
    else:
        c.append("<p>未提供舆论聚类数据（opinion.json 缺失或为空）。</p>")
    if viewpoint_md:
        vmd = md_to_html(viewpoint_md)
        if not vmd.lstrip().startswith("<h"):
            c.append("<h3>视点综合（LLM 分析）</h3>")
        c.append(vmd)
    else:
        c.append('<p class="legend">（未提供视点综合 markdown：建议 LLM 按 opinion.md 提炼各立场论证结构、标注少数派与对立观点、判断焦点转移后以 --viewpoint 注入。）</p>')
    return "\n".join(c)


def _collect_sources(sources, recs):
    """合并显式来源与 normalized 记录中自动收集的 URL（去重，自动部分最多 50）。"""
    srcs = list(sources or [])
    seen = set()
    auto_n = 0
    for r in recs:
        u = r.get("url", "")
        if u and u not in seen:
            seen.add(u)
            srcs.append({"name": "%s·%s" % (PLATFORM_NAMES.get(r.get("platform"), r.get("platform")), r.get("author", "")), "url": u, "grade": "C"})
            auto_n += 1
        if auto_n >= 50:
            break
    return srcs


def sec_sources(sources, recs):
    c = ["<h2>六、来源索引</h2>"]
    srcs = _collect_sources(sources, recs)
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
        c.append('<div class="table-scroll"><table><thead><tr><th>编号</th><th>来源</th><th>URL</th></tr></thead><tbody>')
        for i, s in enumerate(srcs, 1):
            g = str(s.get("grade", "")).strip().upper()
            cls = ' class="source-%s"' % g if g in ("A", "B", "C") else ""
            c.append('<tr id="ref%d"%s><td>[%d]</td><td>%s</td><td><a href="%s" target="_blank" rel="noopener">链接</a></td></tr>'
                     % (i, cls, i, esc(s.get("name", "")), esc(s.get("url", ""))))
        c.append("</tbody></table></div>")
    return "\n".join(c)


def sec_coverage(facts, recs, coverage_md, compliance=None):
    c = ["<h2>七、覆盖完整性声明</h2>"]
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
    return ("<h2>八、方法论</h2>"
            "<p>本报告由 bai-bao-dai（百宝袋）四阶段流水线生成：</p>"
            "<ol><li><strong>采集</strong>：知乎内嵌爬虫 / MediaCrawler（微博·小红书）关键词搜索，仅限公开信息；</li>"
            "<li><strong>事实还原</strong>：多源交叉与证据分级（china_sources 初查 + LLM 逐条裁决：真实/部分真实/失实/证据不足）；</li>"
            "<li><strong>舆论把握</strong>：按立场词典单标签聚类（命中关键词最多者胜），识别 &lt;10% 少数派；</li>"
            "<li><strong>综合报告</strong>：本 HTML 九章结构。</li></ol>"
            '<p class="legend">确定性标记：▲官方确认 / ●多源一致 / △单源存疑。未确认信息不伪装成事实。</p>')


def sec_timestamp(recs):
    c = ["<h2>九、信息与生成时间戳</h2>", '<div class="meta-box"><dl>']
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


def _actors_html(actors):
    """角色小词典：第一章末可选折叠卡组（按 side 分组）。"""
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


def _masthead_html(event, facts, opinion, recs, title, intro):
    """报头区（模板 .masthead/.hero-grid，自动数字 hero-figure）。
    title 可含 <em>（不转义，信任 LLM/用户输入）；intro 按普通文本转义。"""
    c = ['<div class="masthead">', '<div class="hero-grid">', "<div>"]
    c.append('<div class="eyebrow">社会事件综合汇编 · 采集 → 事实还原 → 舆论把握 → 报告</div>')
    c.append("<h1>%s</h1>" % (title or esc(event)))
    if intro:
        c.append('<p class="intro">%s</p>' % esc(intro))
    c.append("</div>")

    items = []
    n_recs = len(recs) if recs else 0
    claims = (facts or {}).get("claims") or []
    dist = (opinion or {}).get("stance_distribution") or []
    if n_recs:
        plat = _platform_counts(recs)
        plat_s = "、".join("%s%d" % (PLATFORM_NAMES.get(p, p)[:3], n) for p, n in plat.items())
        items.append(("%d" % n_recs, "条", "平台采集 · " + (plat_s[:40] or "")))
    if claims:
        cnt = {}
        for cl in claims:
            v = cl.get("verdict", "待核查")
            cnt[v] = cnt.get(v, 0) + 1
        verdict_s = " / ".join("%s%d" % ({k: {"真实": "真实", "部分真实": "部分", "失实": "失实", "证据不足": "不足"}.get(k, k)}[k], v) for k, v in cnt.items())
        items.append(("%d" % len(claims), "条", "事实断言 · " + verdict_s))
    if dist:
        top = dist[0]["stance"]
        items.append(("%d" % len(dist), "个", "立场聚类 · " + top[:22]))
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
                  milestones=None, compliance=None):
    viewpoint_md = _read_md(viewpoint_md)
    coverage_md = _read_md(coverage_md)
    intros = intros or {}
    actors_html = _actors_html(actors or [])
    track_html = _track_html(track or {})
    secs = [
        sec_overview(event, facts, opinion, recs, abstract_text),
        sec_timeline(opinion, recs, milestones),
        sec_facts(facts, extra_md),
        sec_check(facts),
        sec_opinion(opinion, viewpoint_md),
        sec_sources(sources, recs),
        sec_coverage(facts, recs, coverage_md, compliance),
        sec_method(),
        sec_timestamp(recs),
    ]
    titles = ["结论速览", "事件时间线", "核心事实汇编", "事实核查结果",
              "舆论观点综合", "来源索引", "覆盖完整性声明", "方法论", "信息与生成时间戳"]
    cn = "一二三四五六七八九"
    defaults = _default_intros(facts, opinion, sources, recs)
    toc = ['<nav class="toc" aria-label="目录">'] if not nav_drawer else []
    anchored = []
    for i, (sec, title) in enumerate(zip(secs, titles), 1):
        cid = "ch%d" % i
        if not nav_drawer:
            toc.append('<a href="#%s">%s、%s</a>' % (cid, cn[i - 1], title))
        intro = intros.get(str(i)) or defaults.get(str(i))
        if intro:
            sec = sec.replace("</h2>", "</h2>\n<p class=\"chapter-intro\">%s</p>" % esc(intro), 1)
        # P2 可选组件：角色词典挂第一章末，双轨轴挂第二章末
        if i == 1 and actors_html:
            sec += "\n" + actors_html
        if i == 2 and track_html:
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
    mast = _masthead_html(event, facts, opinion, recs, mast_title, mast_intro)
    parts = [mast] + (["".join(toc)] if toc else []) + anchored
    parts.append('<p class="footer">由 bai-bao-dai（百宝袋）生成 · 仅供学习研究</p>')
    return "\n".join(parts)


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
    ap.add_argument("--actors", default="", help="角色小词典 json：[{name,type,side,position,stance,quotes,ref,credibility}]（可选，无则不渲染）")
    ap.add_argument("--track", default="", help="说法×证实双轨轴 json：{\"rows\":[{day,lane:claim|verify,type,text,ref}]}（可选，无则不渲染）")
    ap.add_argument("--intros", default="", help="章节导读覆盖 json：{\"1\":\"…\",\"2\":\"…\",…,\"9\":\"…\",\"appendix\":\"…\"}（缺省按数据程序化生成导航句）")
    ap.add_argument("--title", default="", help="报头主标题（可含 <em> 强调）；缺省用 --event 原文")
    ap.add_argument("--intro", default="", help="报头一句话导语（可选）；留空则不渲染 intro")
    ap.add_argument("--template", default="", help="模板 HTML 路径（默认：技能自带副本 templates/report_template.html；试验/副本验证用）")
    ap.add_argument("--abstract", default="", help="事件摘要两段式 markdown（空行分段：段一事件→进展→舆论；段二真实性→结构提示）")
    ap.add_argument("--timeline-milestones", default="", help="竖式里程碑时间轴 json：[{date,type,tag,title,text,count}]（可选；缺省回退日期×记录数表）")
    ap.add_argument("--nav-drawer", dest="nav_drawer", action=argparse.BooleanOptionalAction, default=True, help="右侧毛玻璃悬浮目录（默认开；--no-nav-drawer 关闭）")
    ap.add_argument("--joint", action="store_true", help="三技能联合任务模式：注入联合 meta 标记，且必须提供 --compliance 合规账本（CONTRACT-joint）")
    ap.add_argument("--compliance", default="", help="采集合规账本 json（联合任务必需）：[{platform,status,block,escalated,user_confirmed,alternative,note}]")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    facts = load_json(args.facts)
    opinion = load_json(args.opinion)
    sources = load_json(args.sources)
    recs = load_normalized(args.normalized)
    intros = load_json(args.intros)
    if not isinstance(intros, dict):
        intros = {}
    actors = load_json(args.actors)
    if not isinstance(actors, list):
        actors = []
    track = load_json(args.track)
    if not isinstance(track, dict):
        track = {}
    milestones = load_json(args.timeline_milestones)
    if not isinstance(milestones, list):
        milestones = []
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
                            milestones=milestones, compliance=compliance)
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
        html = html.replace("</head>", '<meta name="dsh-report" content="joint;contract=joint-v0.1">\n</head>', 1)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print("[report] 已生成 → %s" % args.out)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
