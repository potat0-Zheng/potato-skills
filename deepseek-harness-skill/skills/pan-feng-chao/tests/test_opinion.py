# -*- coding: utf-8 -*-
"""判风潮 · opinion.py 回归（工件契约、边界、可复现、开关不变性）。

两条最重要的不变量（改脚本时必须仍然成立）：
  1. **字段只增不改**：④ 百宝袋装配端按字段读，旧字段名与含义不得变；
  2. **新功能开关不改变既有数字**：`--structure/--candidates/--change-points` 关掉后，
     既有字段必须与默认运行**逐位相同**（这是"增强不改默认路径"的机器判据）。
"""
import contextlib
import io
import json
import os
import unittest

import _util
from _util import SKILL_DIR  # noqa: F401

import opinion
import vocab

WORK = None
CORPUS = None
OPINION = None          # 示例语料 + 词表 + 规则 + 事件 + LLM 标注 的完整运行
OPINION_BARE = None     # 不带规则/事件/LLM 的最小运行
OPINION_NEW_OFF = None  # 与 OPINION 同参数，但三个新功能开关关闭（用于不变性比对）

# ④ 百宝袋 build_report.py 实际读取的字段（改名即打断装配端）
CONTRACT_FIELDS = ["coverage_rate", "coverage_of_judgeable", "judgeable_denominator", "denominator",
                   "stance_distribution", "stance_timeline", "institutional_layer", "residual",
                   "total_records", "event", "note"]
SEGMENT_NAMES = ["纯反应（短句/表情/链接）", "原样转述（长文报道口径）", "未分类（待抽样人工核）"]


def setUpModule():
    global WORK, CORPUS, OPINION, OPINION_BARE, OPINION_NEW_OFF
    WORK = _util.workdir("opinion")
    _util.copy_example(WORK)
    CORPUS = _util.corpus(WORK)
    OPINION = _util.make_opinion(WORK, "opinion_full.json", extra=[
        "--rules", os.path.join(WORK, "opinion_rules.json"),
        "--events", os.path.join(WORK, "events.json"),
        "--llm-labels", os.path.join(WORK, "llm_labels.jsonl"),
        "--total-raw", 40])
    OPINION_BARE = _util.make_opinion(WORK, "opinion_bare.json")
    OPINION_NEW_OFF = _util.make_opinion(WORK, "opinion_new_off.json", extra=[
        "--rules", os.path.join(WORK, "opinion_rules.json"),
        "--total-raw", 40, "--candidates", "off", "--change-points", "off", "--structure", "off"])


class TestInstitutional(unittest.TestCase):
    """B 层判定的验收判据（写入计划的 D1）：宁可偏小，不可污染分母。"""

    FALSE_POSITIVES = ["网友A", "网名不好起", "阿闻", "视频博主小李", "报料人",
                       "都市丽人", "发布君", "观察者小王", "资讯控", "频道主"]
    TRUE_POSITIVES = ["潇湘晨报", "中国新闻网", "央视新闻", "人民日报", "极目新闻"]
    ANONYMIZED = ["潇***报", "某***网", "***报"]

    def setUp(self):
        self.cfg = opinion.load_institutional("")

    def test_nicknames_are_not_institutions(self):
        for au in self.FALSE_POSITIVES:
            hit, why = opinion.is_institutional({"author": au, "content": "普通内容"}, self.cfg)
            self.assertFalse(hit, "%s 被判成机构帖（%s）" % (au, why))

    def test_institutions_and_anonymized_forms_are_caught(self):
        for au in self.TRUE_POSITIVES + self.ANONYMIZED:
            hit, _why = opinion.is_institutional({"author": au, "content": "普通内容"}, self.cfg)
            self.assertTrue(hit, "%s 未被判成机构帖" % au)

    def test_custom_rules_merge_not_replace(self):
        path = os.path.join(WORK, "inst.json")
        _util.save(path, {"name_suffixes": ["示例观察"], "note": "自定义"})
        cfg = opinion.load_institutional(path)
        self.assertTrue(opinion.is_institutional({"author": "某某示例观察", "content": ""}, cfg)[0])
        self.assertTrue(opinion.is_institutional({"author": "示例日报", "content": ""}, cfg)[0])


class TestClassify(unittest.TestCase):
    STANCES = {"甲立场": ["甲词", "共用"], "乙立场": ["乙词", "共用"]}

    def test_most_hits_wins(self):
        label, matched, _neg = opinion.classify("甲词 共用", self.STANCES)
        self.assertEqual(label, "甲立场")
        self.assertEqual(sorted(matched), ["共用", "甲词"])

    def test_tie_breaks_by_dict_order(self):
        label, _m, _n = opinion.classify("甲词 乙词", self.STANCES)
        self.assertEqual(label, "甲立场")      # 平票取词典插入顺序先者

    def test_no_hit_falls_into_other(self):
        label, matched, neg = opinion.classify("今天天气不错", self.STANCES)
        self.assertEqual(label, opinion.OTHER)
        self.assertEqual((matched, neg), ([], []))

    def test_negation_is_flagged_not_reclassified(self):
        # 疑似否定只在"否定词紧邻命中词之前"时标记（脚本用 4 字窗口 + 否定前缀判定）
        st = {"支持加装": ["支持加装"], "一楼反对": ["采光"]}
        label, _m, neg = opinion.classify("不支持加装，一楼采光没了", st)
        self.assertEqual(label, "支持加装")      # 平票取词典顺序先者；否定不改判
        self.assertIn("支持加装", neg)
        # 隔了别的词（"不反对甲词"）不算紧邻修饰 → 不标记
        _l, _m2, neg2 = opinion.classify("不反对甲词", self.STANCES)
        self.assertEqual(neg2, [])

    def test_irony_is_flagged_not_reclassified(self):
        text = "甲词？呵呵，先看看别的吧。"
        label, matched, _n = opinion.classify(text, self.STANCES)
        self.assertEqual(label, "甲立场")       # 反讽不改判
        self.assertTrue(opinion._irony_signals(text, matched))
        self.assertEqual(opinion._irony_signals("甲词很对", matched), [])   # 无线索则不标


class TestArtifacts(unittest.TestCase):
    def setUp(self):
        self.d = _util.load(OPINION)

    def test_contract_fields_present(self):
        for k in CONTRACT_FIELDS:
            self.assertIn(k, self.d, "缺字段 %s（会打断 build_report）" % k)
        for seg in SEGMENT_NAMES:
            self.assertIn(seg, self.d["residual"]["composition"],
                          "残差构成缺段名「%s」（build_report 逐名读取）" % seg)

    def test_summary_agrees_with_main_fields(self):
        sm = {x["stance"]: (x["count"], x["pct"]) for x in self.d["summary"]["stances"]}
        sd = {x["stance"]: (x["count"], x["pct"]) for x in self.d["stance_distribution"]}
        self.assertEqual(sm, sd)
        self.assertEqual(self.d["summary"]["total_records"], self.d["total_records"])
        self.assertEqual(self.d["summary"]["layers"]["A"], self.d["denominator"])

    def test_daily_detail_self_consistent(self):
        for day, v in self.d["stance_timeline_detail"].items():
            self.assertEqual(sum(v["by_stance"].values()), v["denominator"], day)
            self.assertEqual(len(v["ci95"]), len(v["by_stance"]), day)
            self.assertGreaterEqual(v["records_total"], v["denominator"], day)

    def test_timeline_keeps_old_shape_and_excludes_other(self):
        tl = self.d["stance_timeline"]
        self.assertTrue(tl)
        for _day, seg in tl.items():
            self.assertNotIn("未识别", seg)      # 未识别不得作为立场画进图表

    def test_unknown_is_null_not_zero(self):
        bare = _util.load(OPINION_BARE)
        self.assertIsNone(bare["residual"]["split"]["语料治理阶段剔除的无关噪声"])
        self.assertEqual(self.d["residual"]["split"]["语料治理阶段剔除的无关噪声"], 6)  # 40-34

    def test_coverage_norule_ignores_rule_layer(self):
        # 契约口径含"规则层独有"会随规则层漂移；norule 口径必须同值
        a = self.d["coverage_of_judgeable_norule"]
        b = _util.load(OPINION_BARE)["coverage_of_judgeable_norule"]
        self.assertEqual(a, b)

    def test_two_institutional_forms_detected(self):
        self.assertEqual(self.d["institutional_layer"]["count"], 2)   # 示例日报 + 示***报

    def test_duplication_splits_cross_account_from_self_repeat(self):
        dup = self.d["audience_structure"]["duplication"]
        self.assertGreaterEqual(dup["cross_account_dup_records"], 3)      # e009/e017/e018
        self.assertGreaterEqual(dup["same_author_repeat_records"], 3)     # 刷屏小哥 ×3
        self.assertGreaterEqual(dup["cluster_count"], 2)

    def test_candidate_cluster_flags_unnamed_stance(self):
        cand = self.d["candidates"]
        seeds = [c["seed_term"] for c in cand["clusters"]]
        self.assertTrue(any("维护费" in s for s in seeds), "候选簇应含「维护费」：%s" % seeds)
        self.assertTrue(cand["suspected_unnamed_count"] >= 1)

    def test_trend_gate_forbids_on_three_day_short_corpus(self):
        self.assertTrue(self.d["selfcheck"]["trend_claim_forbidden"])
        self.assertEqual(self.d["selfcheck"]["effective_days"], 0)

    def test_stance_comparison_verdicts_are_present(self):
        verdicts = [c["verdict"] for c in self.d["stance_comparisons"]]
        self.assertTrue(verdicts)
        self.assertTrue(any("不得声称" in v for v in verdicts))

    def test_manifest_has_no_timestamp_and_records_inputs(self):
        m = self.d["run_manifest"]
        self.assertTrue(m["inputs"])
        self.assertEqual(sorted(m["args"].keys()), sorted(set(m["args"])))
        blob = json.dumps(m, ensure_ascii=False).lower()
        self.assertNotIn("timestamp", blob)
        self.assertNotIn("generated_at", blob)

    def test_manifest_carries_no_absolute_path_or_user_name(self):
        """E8（v0.7）：工件指纹里**不得**出现绝对路径或本机用户名。

        为什么是硬约束：`opinion.json` 会随报告与**发行包**外流，而 `run_manifest` 是它的一部分。
        旧实现把 `"path": str(path)` 原样写进去（实测形如 `C:\\Users\\<用户名>\\...`），
        等于把本机目录结构与用户名一起发布出去；而指纹的用途只是"内容有没有变"，
        文件名足够回答"同一角色换的是哪个文件"。
        """
        m = self.d["run_manifest"]

        def leaves(o):
            if isinstance(o, dict):
                for v in o.values():
                    for x in leaves(v):
                        yield x
            elif isinstance(o, list):
                for v in o:
                    for x in leaves(v):
                        yield x
            else:
                yield o

        for v in leaves(m):
            s = str(v)
            self.assertNotIn(":\\", s, "指纹里出现盘符路径：%s" % s)
            self.assertNotIn("/Users/", s, "指纹里出现用户目录：%s" % s)
            self.assertNotIn("/home/", s, "指纹里出现用户目录：%s" % s)
        for x in m["inputs"]:
            self.assertNotIn("path", x, "输入指纹仍带 path 字段：%s" % x)
            self.assertTrue(x.get("name"), "输入指纹缺 name：%s" % x)


class TestInvariance(unittest.TestCase):
    """新功能开关不得改动既有字段（逐位比较）。"""
    OLD_KEYS = ["event", "total_records", "denominator", "coverage_rate", "coverage_of_judgeable",
                "judgeable_denominator", "coverage_of_judgeable_norule", "judgeable_breakdown",
                "stance_distribution", "institutional_layer", "residual", "rule_layer",
                "stance_timeline", "timeline", "dictionary_quality", "author_distribution",
                "stance_comparisons", "precision"]

    def test_switching_off_new_features_keeps_old_fields_identical(self):
        a = _util.load(OPINION)
        b = _util.load(OPINION_NEW_OFF)
        for k in self.OLD_KEYS:
            self.assertEqual(json.dumps(a[k], ensure_ascii=False, sort_keys=True),
                             json.dumps(b[k], ensure_ascii=False, sort_keys=True),
                             "开关关闭后既有字段 %s 变了" % k)
        self.assertIsNone(b["candidates"])
        self.assertIsNone(b["change_points"])
        self.assertIsNone(b["audience_structure"])


class TestDeterminism(unittest.TestCase):
    def test_two_runs_are_byte_identical(self):
        p1 = _util.make_opinion(WORK, "det1.json", extra=["--total-raw", 40])
        p2 = _util.make_opinion(WORK, "det2.json", extra=["--total-raw", 40])
        with io.open(p1, "rb") as f1, io.open(p2, "rb") as f2:
            self.assertEqual(f1.read(), f2.read(), "两次运行产物不同（存在非确定因素）")


class TestDigest(unittest.TestCase):
    def test_digest_is_one_screen_and_informative(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = opinion.digest(OPINION)
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertLessEqual(len(out.splitlines()), 30, "摘要过长：会失去省上下文的意义")
        for key in ("四层", "覆盖率", "立场", "告警", "读取建议"):
            self.assertIn(key, out)
        size = os.path.getsize(OPINION)
        self.assertLess(len(out.encode("utf-8")), size, "摘要必须显著小于工件本身")

    def test_digest_survives_missing_fields(self):
        # 老 schema 工件（缺新字段）不得让摘要崩掉
        old = os.path.join(WORK, "old_schema.json")
        _util.save(old, {"event": "老", "total_records": 3, "denominator": 1, "coverage_rate": 33.3,
                         "stance_distribution": [{"stance": "甲", "count": 1, "pct": 100.0}]})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = opinion.digest(old)
        self.assertEqual(rc, 0)
        self.assertIn("—", buf.getvalue())     # 缺失值渲染成「—」，不得印 None


class TestVocab(unittest.TestCase):
    def test_diff_detects_added_removed_renamed(self):
        old = os.path.join(WORK, "v_old.json")
        new = os.path.join(WORK, "v_new.json")
        _util.save(old, {"甲": ["老人上下楼", "采光"], "乙": ["用不上"]})
        _util.save(new, {"甲": ["老人上下楼难", "采光"], "丙": ["房本"]})
        r = vocab.diff(old, new)
        self.assertEqual([x["keyword"] for x in r["added"]], ["房本"])
        self.assertEqual([x["keyword"] for x in r["removed"]], ["用不上"])
        self.assertEqual(r["renamed"][0]["new"], "老人上下楼难")
        self.assertEqual(r["stance_added"], ["丙"])


class TestVocabularyContract(unittest.TestCase):
    """词表契约（A6：自 `bai-bao-dai/tests/test_pipeline.py` 迁入）。

    迁移动因：阶段 3 的脚本已整体搬到本 skill。测试若留在百宝袋，就会去 import 一个
    在那儿已不存在的模块；而这两条契约现在是**本 skill 的行为**，必须在本 skill 的测试里守住：

      1. **缺词表必须报错退出**（不静默全归"未识别"）——静默兜底会让分母变 0，
         报告里就出现「覆盖率 0%」的假象，正是"不静默假成功"的底线；
      2. **单标签 + 整数条数**：各立场条数之和 == 分母，且不得出现小数条数
         （小数条数是按权重摊派的旧实现残留，会让"条数"这一列无法核对）。
    """

    def test_missing_stances_exits_nonzero(self):
        out = os.path.join(WORK, "op_nostances.json")
        log = os.path.join(WORK, "nostances_log")
        rc = _util.run("opinion.py", ["--in", CORPUS, "--out", out, "--event", "无词表"],
                       log_dir=log)
        self.assertNotEqual(rc, 0, "缺词表竟以 0 退出（会把整批语料静默归入未识别）")
        with io.open(os.path.join(log, "out.txt"), encoding="utf-8") as f:
            said = f.read()
        self.assertIn("立场词典", said)

    def test_single_label_integer_counts(self):
        for tag, path in (("bare", OPINION_BARE), ("full", OPINION)):
            d = _util.load(path)
            self.assertTrue(d["stance_distribution"], tag)
            self.assertIsInstance(d["denominator"], int, tag)
            for row in d["stance_distribution"]:
                self.assertIsInstance(row["count"], int, "%s %s" % (tag, row))
                self.assertIsInstance(row["pct"], float, "%s %s" % (tag, row))
            total = sum(r["count"] for r in d["stance_distribution"])
            self.assertLessEqual(total, d["denominator"], tag)
        # 无规则层的最小运行里，单标签占比必须正好用尽分母（不多不少）
        bare = _util.load(OPINION_BARE)
        self.assertEqual(sum(r["count"] for r in bare["stance_distribution"]), bare["denominator"])

    def test_stance_timeline_shape(self):
        """stance_timeline：日期 → {立场: 整数条数}，且不得把"未识别"混成立场。"""
        st = _util.load(OPINION_BARE)["stance_timeline"]
        self.assertTrue(st)
        for day, row in st.items():
            self.assertRegex(day, r"^\d{4}-\d{2}-\d{2}$")
            for name, n in row.items():
                self.assertNotIn("未识别", name)
                self.assertIsInstance(n, int)


if __name__ == "__main__":
    unittest.main()
