# -*- coding: utf-8 -*-
"""query_gov_lib.py 离线测试（无网络）。运行方式：
    cd C:\\Users\\郑懿宸\\.dsh\\skills\\can-guo-shi
    python -m unittest discover -s tests -v

注：临时输出使用本目录下的固定路径文件（沙箱环境禁止 tempfile.mkdtemp 目录内写入），
测试结束尝试删除；残留的 _scratch_*.json 由维护者手动清理或忽略。
"""
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL_DIR, "scripts"))

import query_gov_lib as qgl  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "gov_lib_response.json")
SCRATCH_PARAMS = os.path.join(HERE, "_scratch_params.json")
SCRATCH_OUT = os.path.join(HERE, "_scratch_out.json")


def load_fixture():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def scrub_scratch():
    for p in (SCRATCH_PARAMS, SCRATCH_OUT):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


class FakeResp:
    """模拟 urllib 响应对象（支持 with 上下文）。"""

    def __init__(self, payload):
        self._raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestNormalize(unittest.TestCase):
    """字段映射：API 字段 → 政策条目。"""

    def setUp(self):
        self.data = load_fixture()

    def test_field_mapping(self):
        total, items = qgl.normalize(self.data)
        self.assertEqual(total, 107)  # 顶层 totalCount=0，取各分类之和（12+23+51+21）
        self.assertEqual(len(items), 5)
        first = items[0]
        self.assertEqual(first["doc_no"], "国办发〔2022〕35号")
        self.assertEqual(first["agency"], "国务院办公厅")  # 实测字段为 puborg
        self.assertEqual(first["childtype"], "gongwen")  # 分类键 → childtype
        self.assertIn("复制推广", first["title"])
        self.assertTrue(first["url"].startswith("https://www.gov.cn/"))  # 实测字段为 url

    def test_em_tags_stripped(self):
        _, items = qgl.normalize(self.data)
        for it in items:
            self.assertNotIn("<em>", it["title"])
            self.assertNotIn("</em>", it["title"])
        self.assertIn("营商环境", items[0]["title"])

    def test_gongbao_br_and_agency_clean(self):
        _, items = qgl.normalize(self.data)
        gongbao = [it for it in items if it["childtype"] == "gongbao"][0]
        self.assertNotIn("<br", gongbao["title"])  # 公报标题换行被清洗
        self.assertIsNone(gongbao["doc_no"])
        self.assertIsNone(gongbao["agency"])
        self.assertEqual(gongbao["pubtime_iso"], "2025-06-20")

    def test_otherfile_has_url_no_docno(self):
        _, items = qgl.normalize(self.data)
        other = [it for it in items if it["childtype"] == "otherfile"][0]
        self.assertIsNone(other["doc_no"])
        self.assertTrue(other["url"].startswith("https://www.gov.cn/zhengce/"))

    def test_pubtime_ms_to_iso(self):
        _, items = qgl.normalize(self.data)
        self.assertEqual(items[0]["pubtime_iso"], qgl._iso(1667206500000))
        self.assertRegex(items[0]["pubtime_iso"], r"^\d{4}-\d{2}-\d{2}$")
        # pubtimeStr 兜底解析
        self.assertEqual(qgl._parse_dot_date("2022.10.31"), "2022-10-31")
        self.assertIsNone(qgl._parse_dot_date(""))

    def test_childtype_from_cat_key(self):
        _, items = qgl.normalize(self.data)
        by_type = {it["childtype"] for it in items}
        self.assertEqual(by_type, {"gongwen", "bumenfile", "otherfile", "gongbao"})

    def test_empty_catmap(self):
        total, items = qgl.normalize({"searchVO": {"catMap": {}}})
        self.assertIsNone(total)
        self.assertEqual(items, [])

    def test_bad_pubtime_returns_none(self):
        self.assertIsNone(qgl._iso("not-a-number"))


class TestFetch(unittest.TestCase):
    """请求构造：URL 参数与 UA。"""

    def test_request_params_and_ua(self):
        payload = load_fixture()
        with mock.patch("urllib.request.urlopen", return_value=FakeResp(payload)) as m:
            data = qgl.fetch({"q": "营商环境", "n": 2})
        self.assertEqual(data["searchVO"]["totalCount"], 0)
        req = m.call_args[0][0]
        self.assertIn("t=zhengcelibrary", req.full_url)
        self.assertIn("q=%E8%90%A5%E5%95%86%E7%8E%AF%E5%A2%83", req.full_url)  # URL 编码
        self.assertIn("n=2", req.full_url)
        self.assertEqual(req.get_header("User-agent"), qgl.UA)

    def test_network_error_propagates(self):
        with mock.patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("net down")
        ):
            with self.assertRaises(urllib.error.URLError):
                qgl.fetch({"q": "x"})


class TestMain(unittest.TestCase):
    """端到端（离线）：错误路径诚实降级、成功路径落盘。使用固定路径 scratch 文件。"""

    def setUp(self):
        scrub_scratch()

    def tearDown(self):
        scrub_scratch()

    def test_missing_q_returns_1(self):
        self.assertEqual(qgl.main([]), 1)

    def test_network_fail_writes_fallback(self):
        with mock.patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("down")
        ):
            code = qgl.main(["--q", "营商环境", "--out", SCRATCH_OUT])
        self.assertEqual(code, 1)
        with open(SCRATCH_OUT, encoding="utf-8") as f:
            payload = json.load(f)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["fallback"], "use WebSearch")
        self.assertIn("接口请求失败", payload["error"])

    def test_success_writes_normalized_items(self):
        payload = load_fixture()
        with mock.patch("urllib.request.urlopen", return_value=FakeResp(payload)):
            code = qgl.main(["--q", "营商环境", "--n", "2", "--out", SCRATCH_OUT])
        self.assertEqual(code, 0)
        with open(SCRATCH_OUT, encoding="utf-8") as f:
            result = json.load(f)
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 107)
        self.assertEqual(len(result["items"]), 5)
        self.assertIn("fetched_at", result)

    def test_params_file_input(self):
        payload = load_fixture()
        with open(SCRATCH_PARAMS, "w", encoding="utf-8") as f:
            json.dump({"q": "营商环境", "n": 1, "pubtimeyear": "2022"}, f)
        with mock.patch("urllib.request.urlopen", return_value=FakeResp(payload)):
            code = qgl.main([SCRATCH_PARAMS, SCRATCH_OUT])
        self.assertEqual(code, 0)
        with open(SCRATCH_OUT, encoding="utf-8") as f:
            result = json.load(f)
        self.assertTrue(result["ok"])
        self.assertEqual(result["query"]["pubtimeyear"], "2022")


if __name__ == "__main__":
    unittest.main()
