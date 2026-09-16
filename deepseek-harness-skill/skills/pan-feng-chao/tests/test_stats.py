# -*- coding: utf-8 -*-
"""判风潮 · 统计与结构工具回归（stats.py / structure.py）。

这些是"数字准不准"的地基：区间、差异检验、簇修正、变点、同源文本、候选簇。
每条都用**人造数据 + 已知答案**验证，不依赖示例语料的数值。
"""
import unittest

from _util import SKILL_DIR  # noqa: F401  （确保 sys.path 已指向 scripts/）

import stats
import structure


class TestWilson(unittest.TestCase):
    def test_known_value(self):
        # 21/97 的 Wilson 区间（与正例夹具的 21.7% 同量级）
        self.assertEqual(stats.wilson_pct(21, 97), (14.6, 30.8))

    def test_bounds_and_monotone(self):
        lo, hi = stats.wilson_interval(0, 10)
        self.assertEqual(lo, 0.0)
        self.assertLess(hi, 0.5)                 # 0/10 不该给出近 50% 的上界
        lo2, hi2 = stats.wilson_interval(10, 10)
        self.assertGreater(lo2, 0.5)             # 10/10 同理
        self.assertLessEqual(hi2, 1.0)
        self.assertEqual(stats.wilson_interval(0, 0), (0.0, 1.0))

    def test_scale_interval_clamped(self):
        self.assertEqual(stats.scale_interval((0.0, 0.5), 10), [0, 5])
        self.assertLessEqual(stats.scale_interval((0.0, 1.0), 7)[1], 7)


class TestDifferences(unittest.TestCase):
    def test_newcombe_contains_truth_and_is_wider_than_point(self):
        lo, hi = stats.newcombe_diff_ci(30, 100, 10, 100)
        self.assertLess(lo, 0.20)
        self.assertGreater(hi, 0.20)             # 区间必须覆盖真实差 0.20
        self.assertLess(lo, hi)

    def test_newcombe_symmetric_sign(self):
        a = stats.newcombe_diff_ci(3, 10, 7, 10)
        b = stats.newcombe_diff_ci(7, 10, 3, 10)
        self.assertAlmostEqual(-a[0], b[1], places=6)
        self.assertAlmostEqual(-a[1], b[0], places=6)

    def test_multinomial_diff_is_wider_than_naive_same_n(self):
        # 同一批样本内的两个类别不能用"各自 n 条独立样本"的公式（那是常见错误），
        # 正确方差含协方差项 +2p1p2，故必然比 naive 同-n 口径更宽。
        k1, k2, n = 50, 30, 100
        correct = stats.multinomial_diff_ci(k1, k2, n)
        p1, p2 = k1 / n, k2 / n
        se_naive = ((p1 * (1 - p1) + p2 * (1 - p2)) / n) ** 0.5
        naive_w = 2 * stats.Z95 * se_naive
        self.assertGreater(correct[1] - correct[0], naive_w)
        self.assertLess(correct[0], p1 - p2)
        self.assertGreater(correct[1], p1 - p2)

    def test_diff_verdict_language(self):
        self.assertIn("不得声称", stats.diff_verdict((-0.02, 0.05)))
        self.assertIn("可声称", stats.diff_verdict((0.01, 0.30)))


class TestClustered(unittest.TestCase):
    def test_deff_exceeds_one_when_one_author_dominates(self):
        # 一个作者贡献 10 条且全命中 → 有效样本量远小于 10+10
        r = stats.clustered_proportion([10, 0], [10, 10])
        self.assertIsNotNone(r)
        self.assertGreater(r["design_effect"], 1.0)
        self.assertLess(r["effective_n"], r["n_records"])

    def test_returns_none_for_single_cluster(self):
        self.assertIsNone(stats.clustered_proportion([3], [5]))


class TestStratifiedAndSampling(unittest.TestCase):
    def test_post_stratification_weights_by_layer_size(self):
        r = stats.stratified_proportion([
            {"name": "大层", "N": 900, "n": 10, "k": 9},
            {"name": "小层", "N": 100, "n": 10, "k": 0},
        ])
        self.assertAlmostEqual(r["p_pct"], 81.0, delta=1.0)   # 加权后 ≈ 0.9*0.9
        self.assertGreater(r["effective_n"], 0)

    def test_unsampled_layer_is_disclosed(self):
        r = stats.stratified_proportion([
            {"name": "A", "N": 100, "n": 5, "k": 2},
            {"name": "B", "N": 300, "n": 0, "k": 0},
        ])
        self.assertEqual(r["unsampled_N"], 300)

    def test_required_n_and_neyman_and_hhi(self):
        self.assertLess(stats.required_n(0.5, 0.05), stats.required_n(0.5, 0.02))
        self.assertLessEqual(stats.required_n(0.5, 0.05, N=100), stats.required_n(0.5, 0.05))
        _ney, cmp = stats.neyman_allocation([10, 10], 6)
        self.assertIn("variance_ratio", cmp)
        self.assertEqual(stats.hhi([1, 0, 0]), 1.0)
        self.assertEqual(stats.hhi([1, 1, 1, 1]), 0.25)


class TestReliability(unittest.TestCase):
    def test_perfect_agreement(self):
        k = stats.cohen_kappa([("a", "a"), ("b", "b"), ("a", "a"), ("b", "b")])
        self.assertAlmostEqual(k["kappa"], 1.0)
        self.assertFalse(k["degenerate"])

    def test_all_same_category_is_degenerate(self):
        k = stats.cohen_kappa([("x", "x"), ("x", "x")])
        self.assertTrue(k["degenerate"])

    def test_alpha_bounds(self):
        perfect = stats.krippendorff_alpha_nominal([["a", "a"], ["b", "b"], ["a", "a"]])
        self.assertAlmostEqual(perfect["alpha"], 1.0)
        worst = stats.krippendorff_alpha_nominal([["a", "b"], ["b", "a"], ["a", "b"]])
        self.assertLess(worst["alpha"], 0.1)

    def test_reliability_note_text(self):
        self.assertIn("不可接受", stats.reliability_note(0.2, None))
        self.assertIn("良好", stats.reliability_note(0.9, None))


class TestChangePoints(unittest.TestCase):
    def test_locates_planted_shift(self):
        series = [{"day": "2026-01-%02d" % d, "k": 8, "n": 10} for d in range(1, 7)]
        series += [{"day": "2026-01-%02d" % d, "k": 1, "n": 10} for d in range(7, 13)]
        pts = stats.change_points(series)["change_points"]
        self.assertEqual(len(pts), 1)
        self.assertEqual(pts[0]["day"], "2026-01-07")
        self.assertTrue(pts[0]["strong"])

    def test_flat_series_reports_nothing(self):
        series = [{"day": "2026-02-%02d" % d, "k": 5, "n": 10} for d in range(1, 13)]
        self.assertEqual(stats.change_points(series)["change_points"], [])

    def test_short_series_is_skipped_not_guessed(self):
        series = [{"day": "2026-03-01", "k": 1, "n": 2}]
        self.assertEqual(stats.change_points(series)["change_points"], [])


class TestStructure(unittest.TestCase):
    ROWS = [
        {"platform": "weibo", "id": "1", "author": "甲", "content": "反对加装电梯，一楼采光全没了。"},
        {"platform": "weibo", "id": "2", "author": "乙", "content": "反对加装电梯，一楼采光全没了。"},
        {"platform": "weibo", "id": "3", "author": "丙", "content": "反对加装电梯，一楼采光全没了啊。"},
        {"platform": "weibo", "id": "4", "author": "刷屏", "content": "支持加装电梯，老人上下楼方便。"},
        {"platform": "weibo", "id": "5", "author": "刷屏", "content": "支持加装电梯，老人上下楼方便。"},
        {"platform": "weibo", "id": "6", "author": "丁", "content": "维护费怎么分摊，物业给个说法。"},
        {"platform": "weibo", "id": "7", "author": "戊", "content": "维护费分摊方案要写清楚再谈。"},
        {"platform": "weibo", "id": "8", "author": "己", "content": "维护费谁出，先说明白。"},
        {"platform": "weibo", "id": "9", "author": "庚", "content": "维护费和电费都要先算清楚。"},
    ]

    def test_normalize_strips_noise(self):
        # 链接、@提及、话题标记（只留话题内容）、标点与空白都要去掉；这些是"同文本"判定的噪声
        self.assertEqual(structure.normalize_text("看#话题#这个 @某人 https://a.b/c ！"), "看话题这个")
        self.assertEqual(structure.normalize_text(""), "")

    def test_simhash_identical_and_distance(self):
        a = structure.simhash64("反对加装电梯，一楼采光全没了。")
        b = structure.simhash64("反对加装电梯，一楼采光全没了。")
        c = structure.simhash64("今天天气不错，适合出门散步。")
        self.assertEqual(a, b)
        self.assertGreater(structure.hamming(a, c), 8)

    def test_dup_map_separates_cross_account_from_self_repeat(self):
        m = structure.near_dup_map(
            self.ROWS, lambda r: (r["platform"], r["id"]), lambda r: r["content"])
        s = m["stats"]
        # 甲/乙 是规范化后同文（跨账号）→ 必须落在 cross_account
        self.assertGreaterEqual(s["cross_account_dup_records"], 2)
        # 刷屏 的两条是同一账号重复 → 必须落在 same_author_repeat
        self.assertGreaterEqual(s["same_author_repeat_records"], 2)
        # 丙 只差一个字，但与甲的汉明距离 7 > 阈值 3 → 不并入：这说明
        # **近重复检测是识别下界**（漏判是预期的，关键是"没比较的条数"要披露）
        self.assertEqual(len(m["clusters"]), 2)
        self.assertIn("not_compared_short_or_empty_text", s)

    def test_candidate_clusters_finds_unnamed_seed(self):
        vocab = ["反对加装", "支持加装"]
        r = structure.candidate_clusters(self.ROWS, lambda x: (x["platform"], x["id"]),
                                         lambda x: x["content"], vocab=vocab, min_size=4)
        seeds = [c["seed_term"] for c in r["clusters"]]
        self.assertTrue(any("维护费" in s for s in seeds), "应检出「维护费」候选簇：%s" % seeds)
        self.assertTrue(all(c["suspected_unnamed"] for c in r["clusters"]))

    def test_author_report_and_concentration(self):
        rows = self.ROWS
        rep = structure.author_report(rows, lambda x: (x["platform"], x["id"]),
                                      lambda x: "支持加装" if "支持" in x["content"] else "反对加装",
                                      heavy_threshold=2)
        self.assertEqual(rep["records"], len(rows))
        self.assertEqual(rep["unique_authors"], 8)      # 甲/乙/丙/刷屏/丁/戊/己/庚
        self.assertGreater(rep["concentration"]["hhi_x10000"], 0)
        self.assertEqual(rep["heavy_accounts"]["accounts"], 1)   # 刷屏 2 条 ≥ 阈值 2

    def test_author_stance_distribution_sums_to_100(self):
        r = structure.author_stance_distribution(
            self.ROWS, lambda x: "支持加装" if "支持" in x["content"] else "未识别")
        total = sum(x["pct"] for x in r["by_main_stance"])
        self.assertAlmostEqual(total, 100.0, delta=0.2)


if __name__ == "__main__":
    unittest.main()
