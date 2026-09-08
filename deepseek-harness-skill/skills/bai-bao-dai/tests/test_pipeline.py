# -*- coding: utf-8 -*-
"""bai-bao-dai 流水线离线测试（stdlib unittest）。

覆盖：
1. normalize：知乎 md / MediaCrawler 微博·小红书 jsonl 归一化（含时间字段、微博 content 映射）
2. opinion：单标签聚类（无小数条数、无对立立场并存）
3. build_report：9 章齐全、LLM markdown（viewpoint）正确渲染
4. 工具函数：_fmt_epoch / map_mediacrawler / md_to_html

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

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(SKILL_DIR, "scripts")

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
        self.tmp = tempfile.mkdtemp(prefix="bbd_test_")
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


class TestOpinion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bbd_test_")
        self.norm = os.path.join(self.tmp, "data.jsonl")
        self.stances = os.path.join(self.tmp, "stances.json")
        # 立场词典按事件定制，经 --stances 传入（config 默认已为空）
        with open(self.stances, "w", encoding="utf-8") as f:
            json.dump({
                "支持加装": ["支持加装", "方便老人", "政策也支持", "老人上下楼"],
                "一楼住户反对": ["一楼", "采光", "噪音", "用不上", "不公平"],
                "质疑程序与分摊": ["表决", "费用分摊", "强推", "协商"],
            }, f, ensure_ascii=False, indent=2)
        with open(self.norm, "w", encoding="utf-8") as f:
            for line in [
                {"platform": "zhihu", "type": "answer", "id": "1", "author": "a", "time": "2025-06-01",
                 "content": "支持加装电梯，方便老人出行，政策也支持，但费用分摊要协商透明",
                 "metrics": {"likes": 10}, "url": "u1"},
                {"platform": "zhihu", "type": "answer", "id": "2", "author": "b", "time": "2025-06-01",
                 "content": "一楼完全用不上还要分摊费用，采光和噪音都受影响，太不公平了",
                 "metrics": {"likes": 8}, "url": "u2"},
                {"platform": "zhihu", "type": "answer", "id": "3", "author": "c", "time": "2025-06-02",
                 "content": "程序上应组织全体业主表决，费用分摊没谈拢就强推不合适",
                 "metrics": {"likes": 3}, "url": "u3"},
                {"platform": "zhihu", "type": "answer", "id": "4", "author": "d", "time": "2025-06-02",
                 "content": "随便聊聊，今天天气不错", "metrics": {"likes": 1}, "url": "u4"},
            ]:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_single_label_and_counts(self):
        out = os.path.join(self.tmp, "opinion.json")
        r = run_script("opinion.py", "--in", self.norm, "--out", out, "--event", "事件X",
                       "--stances", self.stances)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(out, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["total_records"], 4)
        # 无小数条数：count 全为整数
        for d in data["stance_distribution"]:
            self.assertIsInstance(d["count"], int)
            self.assertIsInstance(d["pct"], float)
        # 单标签：条数之和 == 总条数
        self.assertEqual(sum(d["count"] for d in data["stance_distribution"]), 4)
        # 四条记录应分属四个不同立场（含"无关/其他"兜底）
        labels = {d["stance"]: d["count"] for d in data["stance_distribution"]}
        self.assertEqual(labels, {"支持加装": 1, "一楼住户反对": 1,
                                  "质疑程序与分摊": 1, "无关/其他": 1})
        # 时间轴
        self.assertEqual(data["timeline"], {"2025-06-01": 2, "2025-06-02": 2})
        # 焦点转移：日期 × 立场（P1-6 新增输出键）
        self.assertEqual(data["stance_timeline"], {
            "2025-06-01": {"支持加装": 1, "一楼住户反对": 1},
            "2025-06-02": {"质疑程序与分摊": 1, "无关/其他": 1},
        })

    def test_missing_stances_exits_nonzero(self):
        """config 默认无词典时，不带 --stances 必须报错退出（不静默全归"无关/其他"）。"""
        out = os.path.join(self.tmp, "opinion.json")
        r = run_script("opinion.py", "--in", self.norm, "--out", out, "--event", "事件X")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("立场词典", r.stdout or "")


class TestBuildReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bbd_test_")
        self.facts = os.path.join(self.tmp, "facts.json")
        with open(self.facts, "w", encoding="utf-8") as f:
            json.dump({"event": "E", "claims": [{"claim": "c1", "verdict": "真实", "reason": "r", "evidence": []}]}, f, ensure_ascii=False)
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

    def test_nine_chapters(self):
        out = os.path.join(self.tmp, "report.html")
        r = run_script("build_report.py", "--event", "事件X", "--facts", self.facts,
                       "--opinion", self.opinion, "--normalized", self.norm,
                       "--viewpoint", self.vp, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(out, encoding="utf-8") as _f:
            html = _f.read()
        chapters = re.findall(r"<h2>(一|二|三|四|五|六|七|八|九)、", html)
        self.assertEqual(len(chapters), 9, f"9 章缺失: {chapters}")
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


class TestOpinionNegation(unittest.TestCase):
    """P1-4：否定修饰防呆标记（疑似反讽/否定仅标记提示，不自动改判立场）。"""

    def test_negated_keyword_flagged(self):
        sys.path.insert(0, SCRIPTS)
        import opinion
        st = {"支持方": ["支持加装"]}
        label, _matched, negated = opinion.classify("我不支持加装电梯", st)
        self.assertEqual(label, "支持方")          # 命中"支持加装"仍归该立场
        self.assertEqual(negated, ["支持加装"])   # 但被"不"紧邻修饰 → 打防呆标记
        label, _matched, negated = opinion.classify("我支持加装电梯", st)
        self.assertEqual(negated, [])             # 无否定修饰 → 不标记
        label, _matched, negated = opinion.classify("毫不支持加装的业主占多数", st)
        self.assertEqual(negated, ["支持加装"])   # "毫不"（毫+不）同样触发
        label, _matched, negated = opinion.classify("邻居都说反对加装，我没意见", {"反对方": ["反对加装"]})
        self.assertEqual(negated, [])             # 仅命中词紧邻前置被否定才标记，不误伤


class TestSelftestOffline(unittest.TestCase):
    """selftest.py --offline：本地工具链最小尝试应全绿退出 0。"""

    def test_offline_ok(self):
        r = run_script("selftest.py", "--offline")
        self.assertEqual(r.returncode, 0, (r.stdout or "")[-800:] + (r.stderr or "")[-800:])
        self.assertIn("normalize", r.stdout or "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
