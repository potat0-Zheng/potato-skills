# -*- coding: utf-8 -*-
"""bai-bao-dai 流水线离线测试（stdlib unittest）。

覆盖：
1. normalize：知乎 md / MediaCrawler 微博·小红书 jsonl 归一化（含时间字段、微博 content 映射）
2. build_report：十章齐全、LLM markdown（viewpoint）正确渲染
3. build_report 降级（A7 回归）：缺字段时**不得**凭空印出测量值
4. 工具函数：_fmt_epoch / map_mediacrawler / md_to_html
5. selftest.py --offline 全绿

**阶段 3 的测试去哪儿了**：`TestOpinion` / `TestOpinionNegation` 已随脚本迁往
`skills/pan-feng-chao/tests/test_opinion.py`（类 `TestVocabularyContract`）。本目录
不再 import 舆论脚本——脚本不在这儿了，留着就是"引用了不存在的模块"。

运行：python -m unittest discover -s tests -v   （在 skill 根目录）
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
SCRIPTS = os.path.join(SKILL_DIR, "scripts")
TMP_FALLBACK = os.path.join(HERE, "_tmp")


def _mkworkdir(prefix):
    """建一个**真的写得进去**的工作目录，返回其路径。

    坑（实测）：不能只看 `tempfile.mkdtemp()` 有没有抛异常——DSH 的 workspace-write 沙箱下
    它**建目录会成功、往里写才被拒**，于是 setUp 里的 `os.makedirs(raw/weibo)` 抛
    PermissionError，整份测试变成 7 个 error。所以必须**探针写一次**才算数。
    顺序：系统临时目录 → 本技能内 `tests/_tmp/`（工作区内，必然可写）。
    """
    candidates = []
    try:
        candidates.append(tempfile.mkdtemp(prefix=prefix))
    except (PermissionError, OSError):
        pass
    fallback = os.path.join(TMP_FALLBACK, prefix + uuid.uuid4().hex[:8])
    os.makedirs(fallback, exist_ok=True)
    candidates.append(fallback)
    for c in candidates:
        try:
            probe = os.path.join(c, ".write_probe")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
            return c
        except (PermissionError, OSError):
            continue
    raise PermissionError("找不到可写工作目录（系统临时目录与 %s 均不可写）" % TMP_FALLBACK)


ZHIHU_MD = """# 老旧小区加装电梯，一楼住户反对怎么办？
> 问题ID：12345678 | 总回答数：2 | 实际爬取：2
> 来源：https://www.zhihu.com/question/12345678
---
## [1] 规划师
- 回答者ID：u1
- 赞同数：1200
- 发布时间：2025-06-01 10:20
- 主页：https://www.zhihu.com/people/u1
- 回答链接：https://www.zhihu.com/question/12345678/answer/a1

支持加装电梯，方便老人出行，政策也支持，但费用分摊要协商透明。
---
## [2] 一楼住户
- 回答者ID：u2
- 赞同数：800
- 发布时间：2025-06-02 14:05
- 主页：https://www.zhihu.com/people/u2
- 回答链接：https://www.zhihu.com/question/12345678/answer/a2

一楼完全用不上还要分摊费用，采光和噪音都受影响，太不公平了！
"""

WEIBO_JSONL = [
    {"note_id": "w1", "content": "支持加装，老人上下楼太难了", "create_date_time": "2025-06-01 08:00",
     "liked_count": "900", "comments_count": "30", "shared_count": "5",
     "note_url": "https://m.weibo.cn/detail/w1", "nickname": "微博用户A", "source_keyword": "老旧小区加装电梯"},
]
XHS_JSONL = [
    {"note_id": "x1", "title": "", "desc": "一楼反对加装电梯", "time": "2025-06-01",
     "liked_count": "500", "comment_count": "20", "share_count": "3",
     "note_url": "https://www.xiaohongshu.com/explore/x1", "nickname": "小红用户C", "source_keyword": "老旧小区加装电梯"},
]

VIEWPOINT_MD = """# 视点综合
- **主流立场**：支持加装
- **对立观点**：一楼住户反对
> 依据：opinion.md
"""

# 最小事实工件：只要 facts.json 可读即可（本目录不再测核查逻辑）
FACTS_MIN = {"event": "E", "claims": [{"claim": "c1", "verdict": "真实", "reason": "r", "evidence": []}]}


def run_script(name, *args):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, name), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env)


def injected_content(html):
    """截取 build_report 注入 CONTENT_START/END 之间的正文片段。

    模板自带 CSS 与开发注释里含组件类名/说明文字（如"角色小词典"/dualtrack），
    对整份 html 做子串断言必然误报；只对注入片段断言才能反映"组件是否真的渲染"。
    """
    start = html.index("<!-- CONTENT_START -->") + len("<!-- CONTENT_START -->")
    end = html.index("<!-- CONTENT_END -->", start)
    return html[start:end]


class TestNormalize(unittest.TestCase):
    def setUp(self):
        self.tmp = _mkworkdir("bbd_test_")
        raw = os.path.join(self.tmp, "raw")
        os.makedirs(os.path.join(raw, "zhihu"))
        os.makedirs(os.path.join(raw, "weibo"))
        os.makedirs(os.path.join(raw, "xiaohongshu"))
        with open(os.path.join(raw, "zhihu", "q.md"), "w", encoding="utf-8") as f:
            f.write(ZHIHU_MD)
        with open(os.path.join(raw, "weibo", "contents.jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(x, ensure_ascii=False) for x in WEIBO_JSONL) + "\n")
        with open(os.path.join(raw, "xiaohongshu", "contents.jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(x, ensure_ascii=False) for x in XHS_JSONL) + "\n")
        self.raw = raw
        self.norm = os.path.join(self.tmp, "normalized", "data.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_normalize_records(self):
        r = run_script("normalize.py", "--in", self.raw, "--out", self.norm, "--event", "事件X")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.norm, encoding="utf-8") as _f:
            recs = [json.loads(l) for l in _f if l.strip()]
        self.assertEqual(len(recs), 4)  # 2 知乎 + 1 微博 + 1 小红书
        by_platform = {x["platform"] for x in recs}
        self.assertEqual(by_platform, {"zhihu", "weibo", "xiaohongshu"})
        # 时间字段已填充
        for rec in recs:
            self.assertTrue(rec["time"], f"time 为空: {rec}")
        # 微博 content 已映射
        wb = next(x for x in recs if x["platform"] == "weibo")
        self.assertIn("老人上下楼", wb["content"])
        # 知乎 url 指向回答链接
        zh = next(x for x in recs if x["platform"] == "zhihu")
        self.assertIn("/answer/a1", zh["url"])


class TestBuildReport(unittest.TestCase):
    def setUp(self):
        self.tmp = _mkworkdir("bbd_test_")
        self.facts = os.path.join(self.tmp, "facts.json")
        with open(self.facts, "w", encoding="utf-8") as f:
            json.dump(FACTS_MIN, f, ensure_ascii=False)
        self.opinion = os.path.join(self.tmp, "opinion.json")
        with open(self.opinion, "w", encoding="utf-8") as f:
            json.dump({"event": "E", "total_records": 2, "stance_distribution": [
                {"stance": "支持加装", "count": 2, "pct": 100.0, "likes": 10, "minority": False,
                 "top_samples": [{"author": "a", "likes": 10, "content": "x", "url": "u"}]}],
                "timeline": {"2025-06-01": 2}}, f, ensure_ascii=False)
        self.norm = os.path.join(self.tmp, "data.jsonl")
        with open(self.norm, "w", encoding="utf-8") as f:
            f.write(json.dumps({"platform": "zhihu", "type": "answer", "id": "1", "author": "a",
                                "time": "2025-06-01 10:00", "content": "x", "metrics": {"likes": 1},
                                "url": "https://www.zhihu.com/question/1/answer/1"}, ensure_ascii=False) + "\n")
        self.vp = os.path.join(self.tmp, "viewpoint.md")
        with open(self.vp, "w", encoding="utf-8") as f:
            f.write(VIEWPOINT_MD)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ten_chapters(self):
        out = os.path.join(self.tmp, "report.html")
        r = run_script("build_report.py", "--event", "事件X", "--facts", self.facts,
                       "--opinion", self.opinion, "--normalized", self.norm,
                       "--viewpoint", self.vp, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(out, encoding="utf-8") as _f:
            html = _f.read()
        # 十章齐全（旧断言只数到「九」，插章后「十、」整章漏检）
        chs = [re.sub(r"<[^>]+>", "", c) for c in re.findall(r"<h2[^>]*>(.*?)</h2>", html)]
        numbered = [c for c in chs if re.match(r"^[一二三四五六七八九十]、", c)]
        self.assertEqual(len(numbered), 10, f"十章缺失：{numbered}")
        # viewpoint markdown 已渲染（非路径）
        self.assertIn("<strong>主流立场</strong>", html)
        self.assertNotIn("viewpoint.md</p>", html)
        self.assertIn("<blockquote>", html)
        # P1 组件（立场条形 / 来源小结 / 覆盖矩阵；无 stance_timeline 与 conflicts 时不渲染对应块）
        self.assertIn('<div class="bars"', html)
        self.assertIn("bar-fill", html)
        self.assertIn('class="stat-line"', html)
        self.assertIn('table class="matrix"', html)
        self.assertNotIn('class="stacks"', html)
        self.assertNotIn('class="conflict"', html)

    def test_p2_optional_blocks(self):
        """P2：无 --actors/--track 不渲染；有则渲染（角色词典 / 双轨时间轴）。"""
        # 无输入 → 两组件均不出现
        out1 = os.path.join(self.tmp, "report_no_p2.html")
        r1 = run_script("build_report.py", "--event", "事件X", "--facts", self.facts,
                        "--opinion", self.opinion, "--normalized", self.norm,
                        "--viewpoint", self.vp, "--out", out1)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        h1 = injected_content(open(out1, encoding="utf-8").read())
        self.assertNotIn("角色小词典", h1)
        self.assertNotIn("dualtrack", h1)
        # 有输入 → 渲染
        act = os.path.join(self.tmp, "actors.json")
        with open(act, "w", encoding="utf-8") as f:
            json.dump([{"name": "A方", "side": "甲", "type": "官方", "credibility": "▲官方",
                        "stance": "坚持 X", "quotes": "q1\nq2", "ref": "[1]"}], f, ensure_ascii=False)
        trk = os.path.join(self.tmp, "track.json")
        with open(trk, "w", encoding="utf-8") as f:
            json.dump({"rows": [
                {"day": "2025-06-01", "lane": "claim", "type": "statement", "text": "说法甲"},
                {"day": "2025-06-02", "lane": "verify", "type": "refute", "text": "证伪", "ref": "[1]"}]},
                f, ensure_ascii=False)
        out2 = os.path.join(self.tmp, "report_p2.html")
        r2 = run_script("build_report.py", "--event", "事件X", "--facts", self.facts,
                        "--opinion", self.opinion, "--normalized", self.norm,
                        "--viewpoint", self.vp, "--actors", act, "--track", trk, "--out", out2)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        h2 = injected_content(open(out2, encoding="utf-8").read())
        self.assertIn("角色小词典", h2)
        self.assertIn("A方", h2)
        self.assertIn("双轨时间轴", h2)
        self.assertIn("cell-verify", h2)
        self.assertIn("mark-caution", h2)  # refute → ✗证伪 徽章


class TestOpinionDegradation(unittest.TestCase):
    """A7 回归：缺字段时**不得**凭空印出测量值。

    背景：旧实现
        `cov_now = float(opinion.get("coverage_of_judgeable") or opinion.get("coverage_rate") or 0)`
    把「字段缺失」和「测得 0%」压成同一个 `0.0`，于是旧 schema 工件的报告里会出现
    「当前归类覆盖率仅 0.0%（可判集合口径）」——一句凭空造出来的测量结论。
    另外旧实现把缺 `spotcheck` 的情况静默 `return ""`，读者会把未校正读数读成带区间的结论。
    """

    def setUp(self):
        self.tmp = _mkworkdir("bbd_degrade_")
        self.facts = os.path.join(self.tmp, "facts.json")
        with open(self.facts, "w", encoding="utf-8") as f:
            json.dump(FACTS_MIN, f, ensure_ascii=False)
        self.norm = os.path.join(self.tmp, "data.jsonl")
        with open(self.norm, "w", encoding="utf-8") as f:
            f.write(json.dumps({"platform": "zhihu", "type": "answer", "id": "1", "author": "a",
                                "time": "2025-06-01 10:00", "content": "x", "metrics": {"likes": 1},
                                "url": "https://www.zhihu.com/q/1/a/1"}, ensure_ascii=False) + "\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _render(self, opinion_obj, name):
        op = os.path.join(self.tmp, name + ".json")
        with open(op, "w", encoding="utf-8") as f:
            json.dump(opinion_obj, f, ensure_ascii=False)
        out = os.path.join(self.tmp, name + ".html")
        r = run_script("build_report.py", "--event", "事件X", "--facts", self.facts,
                       "--opinion", op, "--normalized", self.norm, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(out, encoding="utf-8") as f:
            return injected_content(f.read()), (r.stderr or "")

    @staticmethod
    def _dist():
        return [{"stance": "支持加装", "count": 2, "pct": 100.0, "likes": 10,
                 "minority": False, "top_samples": []}]

    def test_missing_coverage_is_not_faked_as_zero(self):
        body, err = self._render(
            {"event": "E", "total_records": 2, "stance_distribution": self._dist(),
             "stance_timeline": {"2025-06-01": {"支持加装": 2}}}, "nofield")
        self.assertNotIn("覆盖率仅 0.0%", body, "缺字段被填成了 0.0% 的假测量值")
        self.assertIn("—（未提供）", body)          # 缺失只能写"未提供"
        self.assertIn("coverage_rate", err)          # 且必须在 stderr 提示

    def test_coverage_label_matches_actual_field(self):
        """口径标签必须与真正取到的字段一致（旧实现把 coverage_rate 也标成"可判集合口径"）。"""
        body, _ = self._render(
            {"event": "E", "total_records": 945, "denominator": 205, "coverage_rate": 21.7,
             "stance_distribution": self._dist(),
             "stance_timeline": {"2025-06-01": {"支持加装": 2}}}, "rateonly")
        self.assertIn("占全部语料口径", body)
        self.assertNotIn("可判集合口径", body)

    def test_missing_spotcheck_is_disclosed_not_silent(self):
        body, _ = self._render(
            {"event": "E", "total_records": 945, "denominator": 205, "coverage_rate": 21.7,
             "stance_distribution": self._dist()}, "nospot")
        self.assertIn("人工抽检结果（未跑）", body)
        self.assertIn("占比为未校正读数", body)

    def test_no_stance_distribution_renders_no_spotcheck_claim(self):
        """连占比都没有的工件，不得硬凑一句"未校正读数"（缺数据即不渲染）。"""
        body, _ = self._render({"event": "E"}, "empty")
        self.assertNotIn("未校正读数", body)


class TestUtils(unittest.TestCase):
    def test_fmt_epoch_and_mapper(self):
        sys.path.insert(0, SCRIPTS)
        import normalize
        self.assertEqual(normalize._fmt_epoch(1748736000), "2025-06-01 08:00")
        self.assertEqual(normalize._fmt_epoch(1748736000000), "2025-06-01 08:00")  # 毫秒
        self.assertEqual(normalize._fmt_epoch("abc"), "abc")
        m = normalize.map_mediacrawler(WEIBO_JSONL[0])
        self.assertEqual(m["platform"], "weibo")
        self.assertEqual(m["content"], "支持加装，老人上下楼太难了")
        self.assertEqual(m["time"], "2025-06-01 08:00")
        m2 = normalize.map_mediacrawler(XHS_JSONL[0])
        self.assertEqual(m2["platform"], "xiaohongshu")
        self.assertEqual(m2["time"], "2025-06-01")

    def test_cn_counts_and_ms_time(self):
        sys.path.insert(0, SCRIPTS)
        import normalize
        self.assertEqual(normalize._parse_cn_count("3.6万"), 36000)
        self.assertEqual(normalize._parse_cn_count("1.2亿"), 120000000)
        self.assertEqual(normalize._parse_cn_count("7460"), 7460)
        self.assertEqual(normalize._parse_cn_count(""), 0)
        self.assertEqual(normalize._parse_cn_count(None), 0)
        self.assertEqual(normalize._parse_cn_count("abc"), 0)
        # 真实小红书 schema（毫秒时间戳 + 中文计数）
        real_xhs = {"note_id": "n1", "type": "video", "title": "t", "desc": "d",
                    "time": 1780374844000, "liked_count": "3.6万",
                    "comment_count": "718", "share_count": "8024",
                    "note_url": "https://www.xiaohongshu.com/explore/n1", "nickname": "守*",
                    "source_keyword": "加油站"}
        m = normalize.map_mediacrawler(real_xhs)
        self.assertEqual(m["time"], "2026-06-02 12:34")
        self.assertEqual(m["metrics"]["likes"], 36000)
        self.assertEqual(m["metrics"]["comments"], 718)

    def test_md_to_html_inline(self):
        sys.path.insert(0, SCRIPTS)
        import build_report
        h = build_report.md_to_html("- **粗体** [链接](https://x.com) `code`")
        self.assertIn("<strong>粗体</strong>", h)
        self.assertIn('<a href="https://x.com"', h)
        self.assertIn("<code>code</code>", h)

    def test_md_to_html_list_then_quote_well_nested(self):
        """回归：列表后紧跟引用（无空行）时，</ul> 必须先于 <blockquote> 闭合。"""
        sys.path.insert(0, SCRIPTS)
        import build_report
        h = build_report.md_to_html("- a\n- b\n> q")
        self.assertLess(h.index("</ul>"), h.index("<blockquote>"))
        self.assertTrue(h.endswith("</blockquote>"))
        self.assertNotIn("<li>", h[h.index("<blockquote>"):])

    def test_md_to_html_quote_then_list_well_nested(self):
        """回归：引用后紧跟列表（无空行）时，</blockquote> 必须先于 <ul> 闭合。"""
        sys.path.insert(0, SCRIPTS)
        import build_report
        h = build_report.md_to_html("> q\n- a\n- b")
        self.assertLess(h.index("</blockquote>"), h.index("<ul>"))
        self.assertTrue(h.endswith("</ul>"))
        self.assertNotIn("<blockquote>", h[h.index("<ul>"):])

    def test_opt_float_never_invents_zero(self):
        """A7 的最小单元：`_opt_float` 对缺失/垃圾输入必须给 None，不给 0。"""
        sys.path.insert(0, SCRIPTS)
        import build_report
        for bad in (None, "", "abc", [], {}):
            self.assertIsNone(build_report._opt_float(bad), repr(bad))
        self.assertEqual(build_report._opt_float(0), 0.0)        # 真的是 0 才是 0
        self.assertEqual(build_report._opt_float("21.7"), 21.7)  # 数字字符串可解析


class TestSelftestOffline(unittest.TestCase):
    """selftest.py --offline：本地工具链最小尝试应全绿退出 0。"""

    def test_offline_ok(self):
        r = run_script("selftest.py", "--offline")
        self.assertEqual(r.returncode, 0, (r.stdout or "")[-800:] + (r.stderr or "")[-800:])
        self.assertIn("normalize", r.stdout or "")
        # 阶段 3 的步骤已迁出：离线链路不得再出现舆论脚本
        self.assertNotIn("opinion", r.stdout or "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
