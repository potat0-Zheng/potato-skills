# -*- coding: utf-8 -*-
"""判风潮 · spotcheck.py 回归（池口径、硬失败、两种块、HTML 判读单、状态模式）。

核心不变量：
  1. **未分类池 ≡ `opinion.residual.count`**，不等即默认硬失败（exit 2）；
  2. **core（代表性）必判完，enrich（富集）可留空**且不阻塞 `--apply`；
  3. 旧判读单（无 `block` 字段）必须仍能 `--apply`（向后兼容）。
"""
import io
import json
import os
import re
import unittest

import _util
from _util import SKILL_DIR  # noqa: F401

WORK = None
CORPUS = None
STANCES = None
RULES = None
OPINION = None


def setUpModule():
    global WORK, CORPUS, STANCES, RULES, OPINION
    WORK = _util.workdir("spotcheck")
    _util.copy_example(WORK)
    CORPUS = _util.corpus(WORK)
    STANCES = os.path.join(WORK, "stances.json")
    RULES = os.path.join(WORK, "opinion_rules.json")
    OPINION = _util.make_opinion(WORK, "opinion.json")


def _make(kind, out_name, extra=()):
    out = os.path.join(WORK, out_name)
    args = ["--kind", kind, "--in", CORPUS, "--opinion", OPINION, "--stances", STANCES,
            "--out", out, "--make"] + list(extra)
    rc = _util.run("spotcheck.py", args, log_dir=WORK)
    return rc, out


def _apply(out, extra=()):
    args = ["--kind", _util.load(out)["kind"], "--in", CORPUS, "--opinion", OPINION,
            "--stances", STANCES, "--out", out, "--apply"] + list(extra)
    return _util.run("spotcheck.py", args, log_dir=WORK)


def _fill(obj, core_only=True, stance="支持设置快递柜"):
    """把判读结果填进判读单：core 必填；core_only=True 时 enrich 留空。"""
    for i, it in enumerate(obj["items"]):
        if core_only and it.get("block") == "enrich":
            continue
        if obj["kind"] == "unclassified":
            it["has_stance"] = (i % 3 != 0)
            it["stance"] = stance if it["has_stance"] else ""
        else:
            it["verdict"] = "错" if i % 4 == 0 else "对"
    return obj


class TestPoolIdentity(unittest.TestCase):
    def test_unclassified_pool_equals_residual(self):
        rc, out = _make("unclassified", "sc_unc.json", ["--n", 5])
        self.assertEqual(rc, 0)
        d = _util.load(out)
        op = _util.load(OPINION)
        plan = d["sampling_plan"][0]
        self.assertEqual(plan["pool"], op["residual"]["count"])
        self.assertEqual(plan["pool_expected_from_opinion"], op["residual"]["count"])
        self.assertIsNone(plan["pool_mismatch"])
        self.assertGreater(plan["sampled"], 0)

    def test_institutional_records_are_excluded_from_pools(self):
        rc, out = _make("stance", "sc_stance.json", ["--n", 3])
        self.assertEqual(rc, 0)
        d = _util.load(out)
        # 示例里 2 条机构帖含立场词（示例日报/示***报），必须一条都不在 core 里
        inst_ids = {"e013", "e024"}
        picked = {it["id"] for it in d["items"] if it.get("block") == "core"}
        self.assertFalse(picked & inst_ids, "机构帖进了代表性样本：%s" % (picked & inst_ids))

    def test_pool_mismatch_hard_fails_unless_allowed(self):
        # 用少一条未识别记录的语料 + 完整语料的 opinion.json → 池必然不同源
        other = _util.workdir("spotcheck_mismatch")
        _util.copy_example(other, drop_ids=("e015",))
        out = os.path.join(other, "sc.json")
        args = ["--kind", "unclassified", "--in", _util.corpus(other), "--opinion", OPINION,
                "--stances", STANCES, "--out", out, "--make", "--n", 5]
        self.assertEqual(_util.run("spotcheck.py", args, log_dir=other), 2)
        allowed = _util.run("spotcheck.py", args + ["--allow-pool-mismatch"], log_dir=other)
        self.assertEqual(allowed, 0)
        self.assertIsNotNone(_util.load(out)["sampling_plan"][0]["pool_mismatch"])


class TestBlocks(unittest.TestCase):
    def test_core_required_enrich_optional(self):
        rc, out = _make("stance", "sc_two_blocks.json", ["--n", 3])
        self.assertEqual(rc, 0)
        d = _fill(_util.load(out), core_only=True)
        _util.save(out, d)
        self.assertEqual(_apply(out), 0)
        res = _util.load(out)
        self.assertGreaterEqual(res["blocks"]["core"], 3)
        self.assertGreaterEqual(res["enrich"]["n_enrich"], 1)
        self.assertEqual(res["enrich"]["status"], "未判读完成")   # 留空 → 只影响对照，不阻塞
        self.assertIn("per_stance", res)

    def test_missing_core_blocks_apply(self):
        rc, out = _make("unclassified", "sc_incomplete.json", ["--n", 5])
        self.assertEqual(rc, 0)
        self.assertEqual(_apply(out), 2)      # 一条都没填 → 不产出部分结果

    def test_enrich_analysis_when_judged(self):
        rc, out = _make("unclassified", "sc_full.json", ["--n", 5])
        _util.save(out, _fill(_util.load(out), core_only=False))
        self.assertEqual(_apply(out), 0)
        en = _util.load(out)["enrich"]
        self.assertEqual(en["status"], "已完成")
        self.assertIn("verdict", en)
        self.assertIn("diff_ci95_pp", en)

    def test_apply_writes_back_into_opinion(self):
        rc, out = _make("unclassified", "sc_writeback.json", ["--n", 5])
        _util.save(out, _fill(_util.load(out)))
        self.assertEqual(_apply(out), 0)
        op = _util.load(OPINION)
        self.assertIn("spotcheck", op)
        self.assertIn("unclassified", op["spotcheck"])
        self.assertEqual(op["spotcheck"]["unclassified"]["status"], "已完成")


class TestCompatibility(unittest.TestCase):
    def test_legacy_form_without_block_fields(self):
        rc, out = _make("unclassified", "sc_legacy.json", ["--n", 5])
        d = _util.load(out)
        for it in d["items"]:
            for k in ("block", "certainty", "uncertainty_reasons", "author", "day_box", "stratum"):
                it.pop(k, None)
        for k in ("blocks", "n_items_enrich", "time_strata", "run_manifest"):
            d.pop(k, None)
        d = _fill(d)
        _util.save(out, d)
        self.assertEqual(_apply(out), 0)
        res = _util.load(out)
        self.assertEqual(res["blocks"]["enrich"], 0)
        self.assertNotIn("enrich", [k for k, v in res.items() if v])   # 无幽灵字段

    def test_status_mode_needs_only_out(self):
        _rc, out = _make("unclassified", "sc_status.json", ["--n", 5])
        self.assertEqual(_util.run("spotcheck.py", ["--status", "--out", out], log_dir=WORK), 0)
        self.assertEqual(_util.run("spotcheck.py", ["--pending", "--out", out], log_dir=WORK), 0)
        self.assertEqual(_util.run("spotcheck.py", ["--status"], log_dir=WORK), 2)   # 缺 --out → 报错退出

    def test_report_renders(self):
        _rc, out = _make("unclassified", "sc_report.json", ["--n", 5])
        _util.save(out, _fill(_util.load(out), core_only=False))
        self.assertEqual(_apply(out), 0)
        md = out.replace(".json", "_结论.md")
        self.assertEqual(_util.run("spotcheck.py", ["--kind", "unclassified", "--in", CORPUS,
                                                   "--opinion", OPINION, "--out", out,
                                                   "--report"], log_dir=WORK), 0)
        text = io.open(md, encoding="utf-8").read()
        for key in ("未分类段抽样", "编码信度", "富集"):
            self.assertIn(key, text)


class TestHtmlForm(unittest.TestCase):
    def test_html_is_self_contained_and_matches_form(self):
        rc, out = _make("unclassified", "sc_html.json", ["--n", 5, "--html"])
        self.assertEqual(rc, 0)
        html_path = out.replace(".json", ".html")
        self.assertTrue(os.path.exists(html_path))
        html = io.open(html_path, encoding="utf-8").read()
        self.assertIsNone(re.search(r"""(?:src|href)=["']https?://""", html), "含外部资源，不能离线用")
        self.assertIn("导出 JSON", html)
        m = re.search(r"const FORM = (\{.*?\});\nconst OUTNAME", html, re.S)
        self.assertIsNotNone(m, "未找到内嵌表单")
        form = json.loads(m.group(1).replace("<\\/", "</"))
        live = _util.load(out)
        self.assertEqual(len(form["items"]), len(live["items"]))
        self.assertEqual(form["kind"], live["kind"])
        self.assertEqual(os.path.basename(form["_form_path"]), os.path.basename(out))

    def test_exported_marker_mismatch_does_not_crash(self):
        _rc, out = _make("unclassified", "sc_export.json", ["--n", 5])
        d = _fill(_util.load(out))
        d["exported_by"] = "spotcheck-html/v1"
        d["exported_at_items"] = 999            # 故意不一致
        _util.save(out, d)
        self.assertEqual(_apply(out), 0)        # 只警告，不阻塞


if __name__ == "__main__":
    unittest.main()
