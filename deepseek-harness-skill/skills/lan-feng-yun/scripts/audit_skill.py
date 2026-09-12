# -*- coding: utf-8 -*-
"""揽风云 结构体检（audit_skill）——把"结构健康"从口头断言变成可重跑的输出。

为什么需要它：技能经过数十轮追加后，规则副本、失效引用、schema 覆盖率、版本线
都只能靠人肉 grep，于是"改完是否生效"无法证明，结构长期处于黑箱状态。
本脚本把体检做成可重跑工件：每轮改动前后各跑一次，用输出差异作为验收证据。

五张清单：
  A 规则清册   关键规则出现在哪些文件的哪些行（发现多副本）
  B 失效引用   SKILL.md 里的 §N / references / 脚本路径是否真实存在且语义匹配
  C schema 覆盖率  ②应交工件在契约与 SKILL.md 里各有无定义
  D 死代码     scripts/*.py 是否在 SKILL.md 的联合任务命令串里出现
  E 版本线     各文件的版本标记（vN / v0.x / joint-v0.x / theme-v）

用法：
  python audit_skill.py                      # 体检揽风云（默认）
  python audit_skill.py --skill lan-feng-yun
  python audit_skill.py --json               # 机读输出
  python audit_skill.py --root C:\\...\\skills
"""
import argparse
import io
import json
import os
import re
import sys

# 参与体检的"主题文件"（规则可能被复制到这些地方）
THEME_FILES = [
    ("report-theme", "CONTRACT.md"),
    ("report-theme", "CONTRACT-joint.md"),
    ("report-theme", "README.md"),
    ("report-theme", "report_template.html"),
    ("report-theme", "check_report.py"),
    ("bai-bao-dai", "scripts/build_report.py"),
]

# 关键规则 → 识别正则（命中即认为该文件写了这条规则）
RULES = {
    "立场卡组书写限制": r"14\s*个?汉字|≤\s*14|55\s*字|3\s*[–\-~]\s*5\s*步",
    "引用编号体系": r"一\s*URL\s*一编号|引用编号|按首次出现顺序分配编号",
    "来源分级 A/B/C": r"来源等级|source-A|class=\"source-[ABC]\"|\bgrade\b",
    "确定性标记 ▲●△": r"▲\s*官方|mark-official|确定性标记",
    "主体档案防漂移": r"只投影不新增|只引用不判定",
    "统一时效/情景句": r"情景句|确定性走势|动态快照",
    "语料治理剔除披露": r"剔除量|dropped_pct|语料治理",
    "人工抽检": r"抽检|误判率|spotcheck",
}

# 联合任务应交工件 → (识别正则, 定义方 skill)
# 定义方 = 该工件的 schema 与产出说明应落在哪个技能的 SKILL.md。
# 必须按定义方核验：否则体检 po-xu-wang 时会把②的六个工件全算成它的缺口（实测误报 6 项）。
ARTIFACTS = [
    ("timeline.json", r"timeline\.json|--timeline-milestones", "lan-feng-yun"),
    ("sources.json", r"sources\.json|--sources", "lan-feng-yun"),
    ("actors.json", r"actors\.json|--actors", "lan-feng-yun"),
    ("stance_cards.json", r"stance_cards\.json|--stance-cards", "lan-feng-yun"),
    ("coverage.md", r"coverage\.md|--coverage", "lan-feng-yun"),
    ("viewpoint.md", r"viewpoint\.md|--viewpoint", "lan-feng-yun"),
    ("facts.json", r"facts\.json", "po-xu-wang"),
]

# 版本标记识别：先删掉"长得像版本号其实是指标符"的片段，再取版本词。
# 误报来源（实测）：export_report_v2 / report_v1（下划线标识符）、deepseek-v4-pro（模型 slug）。
# 规则：带点的（v0.2.18 / joint-v0.4）一律算；不带点的（贯穿式 v6）必须自成词，后面不能接 -。
VER_STRIP = [
    r"\b[A-Za-z_][A-Za-z0-9_]*_v\d+(?:\.\d+)*\b",          # report_v1 / export_report_v2
    r"\b[A-Za-z][A-Za-z0-9]*-v\d+-[A-Za-z0-9\-]+\b",        # deepseek-v4-pro
]
VER_RE = re.compile(r"(?<![\w.])v\d+(?:\.\d+)*(?![.\w])")


def _versions(t):
    """返回该文本里像版本标记的 vN / vN.N 集合（已剔除标识符与模型 slug 误报）。

    带点的一律算（v0.2.18 / joint-v0.4）；不带点的（贯穿式 v6）必须长得像版本行——
    行首（可带 # - * > 前缀）或后接冒号。否则「路由到 v2 灵活布局」这类行文也会被算成版本。
    """
    for pat in VER_STRIP:
        t = re.sub(pat, " ", t)
    found = set()
    for m in VER_RE.finditer(t):
        s = m.group(0)
        if "." in s:
            found.add(s)
            continue
        line_start = t.rfind("\n", 0, m.start()) + 1
        prefix = t[line_start:m.start()].strip(" \t#-*>")
        if prefix == "" or t[m.end():m.end() + 1] in ("：", ":"):
            found.add(s)
    return sorted(found)


# 已人工确认的"引用错位"误报白名单。
# 语义核验只是字面启发式，仍会有"节号正确但标题措辞不含引用处用词"的假警。
# **当前为空**：v2 轮里原有的两条（§2 / §5）不是靠白名单压掉的，而是回过来修了契约小节标题
# ——§2「证据隔离」→「证据隔离与来源分级」、§5「统一时效」→「统一时效与情景句」，
# 标题本就该覆盖内容，改标题比记豁免更治本。今后只有人工确认无误的真误报才登记在此。
DRIFT_WHITELIST = {}


def read(path):
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def audit(skill="lan-feng-yun", root=None):
    root = root or r"C:\Users\郑懿宸\.dsh"
    sk = os.path.join(root, "skills")
    theme = os.path.join(root, "report-theme")
    out = {"skill": skill, "root": root, "A_rules": [], "B_refs": [], "C_artifacts": [],
           "D_deadcode": [], "E_versions": [], "summary": {}}

    skill_md = os.path.join(sk, skill, "SKILL.md")
    text = read(skill_md)
    if not text:
        out["error"] = "找不到 %s" % skill_md
        return out

    # ---- A 规则清册 ----
    scan = [("skills/%s/SKILL.md" % skill, skill_md)] + \
           [(os.path.join(b, f), os.path.join(theme if b == "report-theme" else sk, b, f))
            for b, f in THEME_FILES]
    for name, pat in RULES.items():
        where = []
        for label, path in scan:
            t = read(path)
            if not t:
                continue
            lines = [i + 1 for i, ln in enumerate(t.splitlines()) if re.search(pat, ln)]
            if lines:
                where.append({"file": label.replace("\\", "/"), "lines": lines[:8]})
        out["A_rules"].append({"rule": name, "files": len(where), "where": where})

    # ---- B 失效引用 ----
    cj_all = read(os.path.join(theme, "CONTRACT-joint.md"))
    heads = re.findall(r"(?m)^##\s+(\d+)\.\s*(.+)$", cj_all)
    for m in re.finditer(r"`?(CONTRACT-joint(?:\.md)?)\s*§\s*(\d+)", text):
        sec = int(m.group(2))
        hit = [h for h in heads if int(h[0]) == sec]
        title = hit[0][1].strip() if hit else ""
        # 语义核验：只查"节号是否存在"不够——§9 存在，但语义可能已漂到别处（实测踩过）。
        # 判据两条（任一条成立即视为匹配）：
        #   ① 引用**前 10 字**里的汉字与目标节标题有交集（原判据，擅长"来源分级见 §2"这类写法）
        #   ② 目标节标题里的**二字词**在引用前后一定范围内出现（补上两类写法：一类前文是虚词、
        #      真线索在引用之后；另一类是引用位于长句末尾，"情景表述"这样的线索被推得很远）。
        #      窗口取左 40 / 右 20：仍然只覆盖引用所在的这一句，不会把邻节的词算进来。
        #   ① 引用**前 10 字**里的二字词与目标节标题有交集（原判据是"汉字集合有交集"，
        #      但单字交集会被"见 / 的 / 为"这类虚词蒙过——实测把错位引用也判成匹配，故收紧为二字词）
        before = text[max(0, m.start() - 10):m.start()]
        around = text[max(0, m.start() - 40):m.end() + 20]
        title_cn = set(re.findall(r"[\u4e00-\u9fff]{2}", title))
        claim_cn = set(re.findall(r"[\u4e00-\u9fff]{2}", before))
        sem1 = bool(claim_cn & title_cn)
        sem2 = any(t in around for t in title_cn)
        semantic_ok = sem1 or sem2
        out["B_refs"].append({"kind": "contract-section", "ref": "§%d" % sec,
                              "line": text[:m.start()].count("\n") + 1,
                              "exists": bool(hit), "title": title,
                              "semantic_ok": semantic_ok,
                              "matched_by": ("前文词交" if sem1 else ("标题词命中" if sem2 else "")),
                              "mentioned_nearby": "".join(re.findall(r"[\u4e00-\u9fff]{1,4}", before))[-8:]})
    for m in re.finditer(r"`?(references/[a-z0-9\-]+\.md)", text):
        rel = m.group(1)
        p = os.path.join(sk, skill, rel)
        out["B_refs"].append({"kind": "reference", "ref": rel, "line": text[:m.start()].count("\n") + 1,
                              "exists": os.path.exists(p), "title": ""})
    for m in re.finditer(r"`?scripts[/\\]([a-z_]+\.py)", text):
        fn = m.group(1)
        p = os.path.join(sk, skill, "scripts", fn)
        out["B_refs"].append({"kind": "script", "ref": "scripts/%s" % fn, "line": text[:m.start()].count("\n") + 1,
                              "exists": os.path.exists(p), "title": ""})

    # ---- C schema 覆盖率 ----
    cj = read(os.path.join(theme, "CONTRACT-joint.md"))
    has_artifacts_section = bool(re.search(r"##\s*11\.\s*\S*产出工件契约", cj))
    for art, pat, owner in ARTIFACTS:
        in_contract = bool(re.search(pat, cj)) or (has_artifacts_section and art in cj)
        has_schema = bool(re.search(re.escape(art) + r"[\s\S]{0,900}?(schema|\{\s*\")", cj)) or \
            (has_artifacts_section and art in cj)
        # 只在"定义方"技能的 SKILL.md 里核验（定义方的产物，别人家不算它的缺口）
        owner_md = read(os.path.join(sk, owner, "SKILL.md"))
        in_skill = bool(re.search(pat, owner_md))
        out["C_artifacts"].append({"artifact": art, "owner": owner, "own": owner == skill,
                                   "in_contract": in_contract, "schema_in_contract": has_schema,
                                   "in_skill_md": in_skill})

    # ---- D 死代码 ----
    # 判据（v0.4 起）：脚本要么出现在联合任务命令串里，要么在"脚本适用路径"表里被显式标为
    # 联合任务适用/不适用——后者是**有意声明**，不算死代码。两处都没有才是真死代码。
    # 脚本清册从被体检技能自己的目录取，不写死名单：写死名单会在体检别的技能时报出不属于它的脚本。
    jm = re.search(r"build_report\.py[^\n]*--joint[\s\S]{0,800}", text)
    joint_blob = jm.group(0) if jm else ""
    sdir = os.path.join(sk, skill, "scripts")
    if not os.path.isdir(sdir):
        sdir = os.path.join(sk, skill, "tools")
    script_files = sorted(f for f in os.listdir(sdir) if f.endswith(".py")) if os.path.isdir(sdir) else []
    for fn in script_files:
        mentions = text.count(fn)
        in_joint = fn in joint_blob
        # 查找脚本名所在的表格行，判断是否显式声明了联合任务适用性
        row = next((ln for ln in text.splitlines() if fn in ln and "|" in ln), "")
        declared = bool(row) and ("联合任务" in row or "独立态" in row or "❌" in row or "✅" in row)
        applicable = None
        if row:
            cells = [c.strip() for c in row.split("|")]
            applicable = not any("❌" in c for c in cells)  # 任一列标 ❌ 即视为已声明不适用
        # 死代码判据：既不在联合命令串里，SKILL.md 正文也一次都没提到。
        # 只是"表格里没标联合适用性"不算死代码——不同技能的表头约定不同，写死列位曾误报 6 项。
        out["D_deadcode"].append({"script": fn, "mentions_in_skill": mentions,
                                  "used_in_joint_cmd": in_joint, "documented": mentions > 0,
                                  "declared_in_table": declared, "joint_applicable": applicable})

    # ---- E 版本线 ----
    vers = {}
    for label, path in [("skills/%s/SKILL.md" % skill, skill_md),
                        ("report-theme/CONTRACT-joint.md", os.path.join(theme, "CONTRACT-joint.md")),
                        ("report-theme/CONTRACT.md", os.path.join(theme, "CONTRACT.md")),
                        ("report-theme/report_template.html", os.path.join(theme, "report_template.html")),
                        ("skills/%s/CHANGELOG.md" % skill, os.path.join(sk, skill, "CHANGELOG.md"))]:
        t = read(path)
        if not t:
            continue
        vers[label.replace("\\", "/")] = _versions(t)
    out["E_versions"] = [{"file": k, "versions": v} for k, v in vers.items()]

    # ---- 汇总 ----
    # 只有"两个文件各自都在**定义**这条规则"才算多副本。判据：两处命中行数都不少于 3
    # （1–2 行多为**引用性提及**：如 build_report.py 的注释/常量名，属消费者不属副本）。
    def _is_copied(r):
        if r["files"] < 2:
            return False
        counts = sorted((len(w["lines"]) for w in r["where"]), reverse=True)
        return counts[1] >= 3
    dup = [r for r in out["A_rules"] if _is_copied(r)]
    broken = [r for r in out["B_refs"] if not r["exists"]]
    # 语义核验是启发式：只做"提及对象 vs 目标标题"的字面重叠，会漏报也会误报，
    # 故单列为"待人工确认"，不混进 error——本脚本不是语义验证器。
    # 人工确认过的误报进 DRIFT_WHITELIST，从计数里剔除（但仍逐条打印，不隐藏）。
    drift_all = [r for r in out["B_refs"] if r["exists"] and r["kind"] == "contract-section"
                 and not r.get("semantic_ok", True)]
    for r in drift_all:
        r["whitelisted"] = (skill, r["ref"]) in DRIFT_WHITELIST
        r["whitelist_note"] = DRIFT_WHITELIST.get((skill, r["ref"]), "")
    drift = [r for r in drift_all if not r["whitelisted"]]
    miss = [a for a in out["C_artifacts"] if not (a["in_contract"] and a["in_skill_md"])]
    dead = [d for d in out["D_deadcode"]
            if not d["used_in_joint_cmd"] and not d.get("documented")]
    out["summary"] = {
        "规则多副本数": len(dup),
        "失效引用数": len(broken),
        "待人工确认的引用错位": len(drift),
        "已确认误报（白名单）": len(drift_all) - len(drift),
        "schema 未双处定义数": len(miss),
        "联合任务未用脚本数": len(dead),
        "版本标记种类数": len({v for x in out["E_versions"] for v in x["versions"]}),
    }
    return out


def render(o):
    L = ["# 结构体检：%s" % o["skill"], ""]
    s = o.get("summary", {})
    L.append("## 汇总")
    for k, v in s.items():
        L.append("- %s：**%s**" % (k, v))
    L.append("")
    L.append("## A 规则清册（*＝两处各自都在定义，即多副本）")
    for r in o["A_rules"]:
        counts = sorted((len(w["lines"]) for w in r["where"]), reverse=True)
        copied = len(counts) >= 2 and counts[1] >= 3
        mark = " ⚠ 多副本" if copied else ("  （另一方为引用性提及）" if r["files"] > 1 else "")
        L.append("- %s：%d 个文件%s" % (r["rule"], r["files"], mark))
        for w in r["where"]:
            L.append("    - %s  L%s" % (w["file"], ",".join(str(x) for x in w["lines"][:5])))
    L.append("")
    L.append("## B 引用核验")
    for r in o["B_refs"]:
        if not r["exists"]:
            L.append("- \u274c error\uff5cL%d %s\uff08\u76ee\u6807\u4e0d\u5b58\u5728\uff09" % (r["line"], r["ref"]))
        elif r["kind"] == "contract-section" and not r.get("semantic_ok", True):
            if r.get("whitelisted"):
                L.append("- ✅ 已确认误报｜L%d %s：%s" % (r["line"], r["ref"], r.get("whitelist_note", "")))
            else:
                L.append("- \u26a0 \u5f85\u4eba\u5de5\u786e\u8ba4\uff5cL%d %s\uff1a\u9644\u8fd1\u63d0\u53ca\u300c%s\u300d\uff0c\u8be5\u8282\u6807\u9898\u4e3a\u300c%s\u300d"
                         % (r["line"], r["ref"], r.get("mentioned_nearby", ""), r["title"]))
    if all(r["exists"] and (r.get("semantic_ok", True) or r.get("whitelisted")) for r in o["B_refs"]):
        L.append("- \u5168\u90e8\u6709\u6548\u4e14\u5b57\u9762\u5339\u914d\uff08\u6216\u5df2\u786e\u8ba4\u8bef\u62a5\uff09")
    L.append("")
    L.append("## C schema 覆盖率")
    for a in o["C_artifacts"]:
        flag = "✅" if (a["in_contract"] and a["in_skill_md"]) else "⚠"
        own = "" if a.get("own", True) else "（定义方 %s）" % a.get("owner", "?")
        L.append("- %s %s%s｜契约:%s  schema:%s  SKILL.md:%s"
                 % (flag, a["artifact"], own, a["in_contract"], a["schema_in_contract"], a["in_skill_md"]))
    L.append("")
    L.append("## D 脚本使用")
    for d in o["D_deadcode"]:
        if d.get("declared_in_table"):
            tag = "✅ 已声明适用性"
        elif d["used_in_joint_cmd"]:
            tag = "✅ 联合任务命令串内"
        elif d.get("documented"):
            tag = "✅ 已在 SKILL.md 文档化"
        else:
            tag = "⚠ 未在命令串、也未文档化（疑似死代码）"
        L.append("- %s｜%s｜SKILL.md 提及 %d 次" % (tag, d["script"], d["mentions_in_skill"]))
    L.append("")
    L.append("## E 版本线")
    for v in o["E_versions"]:
        L.append("- %s：%s" % (v["file"], "、".join(v["versions"]) or "（无）"))
    L.append("")
    L.append("> 未核查项：本脚本只做**可机判**的清点，不判断规则内容是否自洽、不验证工件内容质量。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill", default="lan-feng-yun")
    ap.add_argument("--root", default=r"C:\Users\郑懿宸\.dsh")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default="", help="报告输出路径（缺省打印到 stdout）")
    args = ap.parse_args()
    o = audit(args.skill, args.root)
    txt = json.dumps(o, ensure_ascii=False, indent=2) if args.json else render(o)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with io.open(args.out, "w", encoding="utf-8") as f:
            f.write(txt)
        print("[audit] → %s" % args.out)
    else:
        print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
