# -*- coding: utf-8 -*-
"""共享 Markdown → HTML 渲染器（report-theme 版，供装配器/脚本使用）。

支持子集（与 CONTRACT.md §3 一致）：
- 标题    # .. ######（渲染为 h3..h8，避开章级 h2）
- 表格    GFM 管道表：表头行 + --- 分隔行（:--- / :---: / ---: 映射左/中/右对齐），
          渲染为 <table><thead><tbody>，与 report_template.html 表格样式（斑马纹/打印表头）对齐
- 列表    无序列表 - / *
- 引用    >
- 行内    **粗体** *斜体* `代码` [文本](http…)
- 段落

自包含、零依赖（仅标准库）。build_report.py 内的 md_to_html 与此同算法（技能内自包含副本）。
"""
import re


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _inline(text):
    """行内 markdown。text 需已 esc()。"""
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
                  r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    return text


def _split_row(s):
    """拆一行管道表单元格：容忍可选的左右外框 |。"""
    s = s.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [p.strip() for p in s.split("|")]


def _table_from(lines, i):
    """从 lines[i]（疑似表头行，以 | 开头）解析管道表。

    返回 (html, 下一未消费行下标)；若下一非空行不是 --- 分隔行则返回 (None, i)。
    """
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

    def _cell(text, col):
        a = aligns[col] if col < len(aligns) and aligns[col] else ""
        style = ' style="text-align:%s"' % a if a else ""
        return "<td%s>%s</td>" % (style, _inline(esc(text)))

    ths = "".join(
        "<th%s>%s</th>" % (' style="text-align:%s"' % aligns[k] if k < len(aligns) and aligns[k] else "",
                           _inline(esc(heads[k] if k < len(heads) else "")))
        for k in range(len(heads))
    )
    rows = []
    k = j + 1
    while k < len(lines) and lines[k].strip().startswith("|"):
        parts = _split_row(lines[k])
        tds = "".join(_cell(parts[c] if c < len(parts) else "", c)
                      for c in range(len(heads)))
        rows.append("<tr>%s</tr>" % tds)
        k += 1
    html = ("<table>\n<thead>\n<tr>%s</tr>\n</thead>\n<tbody>\n%s\n</tbody>\n</table>"
            % (ths, "\n".join(rows)))
    return html, k


def _strip_head_prefix(text):
    t = (text or "").strip()
    t = re.sub(r"^[一二三四五六七八九十]+、", "", t)
    t = re.sub(r"^\d+\s*[.、．)）]\s*", "", t)
    return t


def md_to_html(md):
    """极简 markdown → HTML（标题/表格/列表/引用/段落 + 行内样式）。"""
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
        # 管道表（优先于其他块类型判定）
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
            out.append("<h%d>%s</h%d>" % (lvl + 2, _inline(esc(_strip_head_prefix(m.group(2)))), lvl + 2))
            i += 1
            continue
        if re.match(r"^\s*[-*]\s+", line):
            if in_quote:
                out.append("</blockquote>"); in_quote = False
            if not in_list:
                out.append("<ul>"); in_list = True
            item = re.sub(r"^\s*[-*]\s+", "", line)
            out.append("<li>%s</li>" % _inline(esc(item)))
            i += 1
            continue
        if re.match(r"^\s*>\s?", line):
            if in_list:
                out.append("</ul>"); in_list = False
            if not in_quote:
                out.append("<blockquote>"); in_quote = True
            out.append(_inline(esc(re.sub(r"^\s*>\s?", "", line))))
            i += 1
            continue
        in_list, in_quote = _flush(out, in_list, in_quote)
        out.append("<p>%s</p>" % _inline(esc(line)))
        i += 1
    in_list, in_quote = _flush(out, in_list, in_quote)
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    sample = (
        "| 立场 | 条数 | 占比 |\n"
        "| :--- | ---: | :---: |\n"
        "| 甲 | 12 | 40% |\n"
        "| 乙 | 18 | 60% |\n"
    )
    print(md_to_html(sample))
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            print(md_to_html(f.read()))
