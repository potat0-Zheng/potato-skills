# -*- coding: utf-8 -*-
"""bai-bao-dai 综合报告生成（9 章结构）。

读 facts.json / opinion.json / normalized jsonl / sources.json，
用 templates/report_template.html 渲染 report.html。章节：
  一、结论速览      二、事件时间线      三、核心事实汇编
  四、事实核查结果  五、舆论观点综合    六、来源索引
  七、覆盖完整性声明 八、方法论          九、信息与生成时间戳

LLM 深度内容（视点综合 / 覆盖完整性补充 / 事实还原正文）通过
--viewpoint / --coverage / --extra 三个 markdown 文件注入并渲染为 HTML。

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
from datetime import datetime

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(SKILL_DIR, "templates", "report_template.html")

PLATFORM_NAMES = {"zhihu": "知乎", "weibo": "微博", "xiaohongshu": "小红书", "unknown": "未知"}
VERDICT_ORDER = ["真实", "部分真实", "失实", "证据不足", "待核查"]


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def load_json(path):
    if not path or not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _inline(text):
    """行内 markdown：加粗/斜体/链接/行内代码。text 需已 esc()。"""
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    return text


def md_to_html(md):
    """极简 markdown → HTML（标题/列表/引用/加粗/斜体/链接/段落）。"""
    lines, out, in_list, in_quote = md.split("\n"), [], False, False
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            if in_list:
                out.append("</ul>"); in_list = False
            if in_quote:
                out.append("</blockquote>"); in_quote = False
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            if in_list:
                out.append("</ul>"); in_list = False
            if in_quote:
                out.append("</blockquote>"); in_quote = False
            lvl = len(m.group(1))
            out.append(f"<h{lvl + 2}>{_inline(esc(m.group(2)))}</h{lvl + 2}>")
            continue
        if re.match(r"^\s*[-*]\s+", line):
            if not in_list:
                out.append("<ul>"); in_list = True
            item = re.sub(r"^\s*[-*]\s+", "", line)
            out.append(f"<li>{_inline(esc(item))}</li>")
            continue
        if re.match(r"^\s*>\s?", line):
            if not in_quote:
                out.append("<blockquote>"); in_quote = True
            out.append(_inline(esc(re.sub(r"^\s*>\s?", "", line))))
            continue
        if in_list:
            out.append("</ul>"); in_list = False
        if in_quote:
            out.append("</blockquote>"); in_quote = False
        out.append(f"<p>{_inline(esc(line))}</p>")
    if in_list:
        out.append("</ul>")
    if in_quote:
        out.append("</blockquote>")
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
def sec_overview(event, facts, opinion, recs):
    c = ["<h2>一、结论速览</h2>", '<div class="meta-box"><dl>']
    c.append(f"<dt>事件</dt><dd><strong>{esc(event)}</strong></dd>")
    total = len(recs)
    c.append(f"<dt>采集量</dt><dd>{total} 条记录（{', '.join('%s %d' % (PLATFORM_NAMES.get(p, p), n) for p, n in _platform_counts(recs).items()) or '无'}）</dd>")
    if facts and facts.get("claims"):
        cnt = {v: 0 for v in VERDICT_ORDER}
        for cl in facts["claims"]:
            v = cl.get("verdict", "待核查")
            cnt[v] = cnt.get(v, 0) + 1
        verdict_summary = "；".join(f"{v} {n}" for v, n in cnt.items() if n)
        c.append(f"<dt>事实核查</dt><dd>共 {len(facts['claims'])} 条断言 → {esc(verdict_summary or '待核查')}</dd>")
    if opinion and opinion.get("stance_distribution"):
        top = opinion["stance_distribution"][0]
        c.append(f"<dt>舆论主立场</dt><dd>{esc(top['stance'])}（{top['pct']}% · {top['count']} 条）</dd>")
        minority = [d["stance"] for d in opinion["stance_distribution"] if d.get("minority")]
        if minority:
            c.append(f"<dt>少数派</dt><dd>{esc('、'.join(minority))}</dd>")
    if opinion and opinion.get("timeline"):
        ts = sorted(opinion["timeline"].keys())
        c.append(f"<dt>时间跨度</dt><dd>{esc(ts[0])} ~ {esc(ts[-1])}</dd>")
    c.append("</dl></div>")
    c.append('<p class="legend">确定性标记：▲官方确认 / ●多源一致 / △单源存疑。本页由脚本自动汇总，深度结论见后续章节。</p>')
    return "\n".join(c)


def _platform_counts(recs):
    out = {}
    for r in recs:
        out[r.get("platform", "unknown")] = out.get(r.get("platform", "unknown"), 0) + 1
    return out


def sec_timeline(opinion, recs):
    c = ["<h2>二、事件时间线</h2>"]
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
        c.append("<table><tr><th>日期</th><th>记录数</th></tr>")
        for day in sorted(tl):
            c.append(f"<tr><td>{esc(day)}</td><td>{tl[day]}</td></tr>")
        c.append("</table>")
    return "\n".join(c)


def sec_facts(facts, extra_md):
    c = ["<h2>三、核心事实汇编</h2>"]
    if facts and facts.get("claims"):
        c.append("<table><tr><th>#</th><th>断言</th><th>结论</th><th>说明</th><th>证据源</th></tr>")
        for i, cl in enumerate(facts["claims"], 1):
            ev = "、".join(e.get("source", "") for e in cl.get("evidence", [])) or "—"
            c.append("<tr><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                i, esc(cl.get("claim", "")), esc(cl.get("verdict", "待核查")),
                esc(cl.get("reason", "")), esc(ev)))
        c.append("</table>")
    else:
        c.append("<p>未提供事实核查数据（facts.json 缺失或为空）。</p>")
    return "\n".join(c)


def sec_check(facts):
    c = ["<h2>四、事实核查结果</h2>"]
    if facts and facts.get("claims"):
        c.append("<table><tr><th>断言</th><th>结论</th><th>理由</th></tr>")
        for cl in facts["claims"]:
            c.append("<tr><td>%s</td><td><strong>%s</strong></td><td>%s</td></tr>" % (
                esc(cl.get("claim", "")), esc(cl.get("verdict", "待核查")), esc(cl.get("reason", ""))))
        c.append("</table>")
        c.append('<p class="legend">结论由 LLM 结合多源证据裁决：真实 / 部分真实 / 失实 / 证据不足 / 待核查。</p>')
    else:
        c.append("<p>无核查记录。</p>")
    return "\n".join(c)


def sec_opinion(opinion, viewpoint_md):
    c = ["<h2>五、舆论观点综合</h2>"]
    if opinion and opinion.get("stance_distribution"):
        c.append("<table><tr><th>立场</th><th>占比</th><th>条数</th><th>少数派</th><th>高赞代表（前3）</th></tr>")
        for d in opinion["stance_distribution"]:
            tops = "；".join("[赞%s] %s" % (s["likes"], esc(s["content"][:40]))
                             for s in d.get("top_samples", []))
            c.append("<tr><td>%s</td><td>%s%%</td><td>%d</td><td>%s</td><td>%s</td></tr>" % (
                esc(d["stance"]), d["pct"], d["count"],
                "是" if d.get("minority") else "—", tops))
        c.append("</table>")
        if opinion.get("timeline"):
            c.append("<p>焦点时间分布见第二章时间线。</p>")
    else:
        c.append("<p>未提供舆论聚类数据（opinion.json 缺失或为空）。</p>")
    if viewpoint_md:
        c.append("<h3>视点综合（LLM 分析）</h3>")
        c.append(md_to_html(viewpoint_md))
    else:
        c.append('<p class="legend">（未提供视点综合 markdown：建议 LLM 按 opinion.md 提炼各立场论证结构、标注少数派与对立观点、判断焦点转移后以 --viewpoint 注入。）</p>')
    return "\n".join(c)


def sec_sources(sources, recs):
    c = ["<h2>六、来源索引</h2>"]
    srcs = list(sources or [])
    # 自动收集 normalized 记录中的 URL（去重，最多 50）
    auto = []
    seen = set()
    for r in recs:
        u = r.get("url", "")
        if u and u not in seen:
            seen.add(u)
            auto.append({"name": "%s·%s" % (PLATFORM_NAMES.get(r.get("platform"), r.get("platform")), r.get("author", "")), "url": u})
        if len(auto) >= 50:
            break
    srcs = srcs + auto
    if not srcs:
        c.append("<p>无来源记录。</p>")
    else:
        c.append("<table><tr><th>编号</th><th>来源</th><th>URL</th></tr>")
        for i, s in enumerate(srcs, 1):
            c.append('<tr id="ref%d"><td>[%d]</td><td>%s</td><td><a href="%s" target="_blank" rel="noopener">链接</a></td></tr>'
                     % (i, i, esc(s.get("name", "")), esc(s.get("url", ""))))
        c.append("</table>")
    return "\n".join(c)


def sec_coverage(facts, recs, coverage_md):
    c = ["<h2>七、覆盖完整性声明</h2>"]
    c.append("<table><tr><th>平台/信源</th><th>状态</th></tr>")
    counts = _platform_counts(recs)
    for p, name in PLATFORM_NAMES.items():
        if p == "unknown":
            continue
        if counts.get(p):
            c.append(f"<tr><td>{name}</td><td>已采集 {counts[p]} 条</td></tr>")
        else:
            c.append(f"<tr><td>{name}</td><td>本次未采集（凭证缺失/未部署/未请求，需在采集阶段确认原因）</td></tr>")
    if facts and facts.get("claims"):
        no_ev = sum(1 for cl in facts["claims"] if not cl.get("evidence"))
        c.append(f"<tr><td>事实证据</td><td>{len(facts['claims'])} 条断言中 {no_ev} 条无自动证据（依赖 LLM 多源核查）</td></tr>")
    c.append("</table>")
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


def build_content(event, facts, opinion, sources, recs, viewpoint_md, coverage_md, extra_md):
    viewpoint_md = _read_md(viewpoint_md)
    coverage_md = _read_md(coverage_md)
    parts = [
        sec_overview(event, facts, opinion, recs),
        sec_timeline(opinion, recs),
        sec_facts(facts, extra_md),
        sec_check(facts),
        sec_opinion(opinion, viewpoint_md),
        sec_sources(sources, recs),
        sec_coverage(facts, recs, coverage_md),
        sec_method(),
        sec_timestamp(recs),
    ]
    if extra_md and os.path.exists(extra_md):
        with open(extra_md, "r", encoding="utf-8-sig") as f:
            parts.append("<h2>附录：补充说明</h2>" + md_to_html(f.read()))
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
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    facts = load_json(args.facts)
    opinion = load_json(args.opinion)
    sources = load_json(args.sources)
    recs = load_normalized(args.normalized)

    with open(TEMPLATE, "r", encoding="utf-8") as f:
        html = f.read()

    content = build_content(args.event, facts, opinion, sources, recs,
                            args.viewpoint, args.coverage, args.extra)
    html = html.replace("<!-- TITLE -->", esc(args.event) + " · 综合分析报告")
    html = html.replace("<!-- CONTENT_START -->", "<!-- CONTENT_START -->\n" + content + "\n<!-- CONTENT_END -->")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print("[report] 已生成 → %s" % args.out)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
