# -*- coding: utf-8 -*-
"""query_flk.py 离线测试（无网络）。运行方式：
    cd C:\\Users\\郑懿宸\\.dsh\\skills\\can-guo-shi
    python -m unittest discover -s tests -v
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

import query_flk as qflk  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "flk_list_response.json")
SCRATCH_OUT = os.path.join(HERE, "_scratch_flk_out.json")


def load_fixture():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def scrub():
    try:
        os.remove(SCRATCH_OUT)
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


class TestNormalizeList(unittest.TestCase):
    def setUp(self):
        self.data = load_fixture()

    def test_field_mapping_and_clean(self):
        total, items = qflk.normalize_list(self.data)
        self.assertEqual(total, 9)
        self.assertEqual(len(items), 3)
        first = items[0]
        self.assertEqual(first["title"], "中华人民共和国行政处罚法")  # 高亮标签已剥离
        self.assertEqual(first["kind"], "法律")
        self.assertEqual(first["agency"], "全国人民代表大会")
        self.assertEqual(first["pub_date"], "2021-01-22")
        self.assertEqual(first["eff_date"], "2021-07-15")
        self.assertEqual(first["status_code"], 3)
        self.assertEqual(first["status_label"], "现行有效")
        self.assertIn("ff8080817703add20177373df6a43e33", first["bbbs"])
        self.assertTrue(first["url"].startswith("https://flk.npc.gov.cn/detail?id="))

    def test_status_labels(self):
        _, items = qflk.normalize_list(self.data)
        self.assertEqual(items[1]["status_label"], "已失效或已被修改（推定）")  # sxx=2
        self.assertEqual(items[2]["status_label"], "已废止（推定）")  # sxx=1
        self.assertIsNone(items[2]["eff_date"])  # sxrq 为 null

    def test_empty_rows(self):
        total, items = qflk.normalize_list({"total": 0, "rows": []})
        self.assertEqual(total, 0)
        self.assertEqual(items, [])


class TestFetchList(unittest.TestCase):
    def test_post_body_and_headers(self):
        payload = load_fixture()
        with mock.patch("urllib.request.urlopen", return_value=FakeResp(payload)) as m:
            data = qflk.fetch_list({"q": "行政处罚法", "sxx": "3", "searchRange": 1})
        self.assertEqual(data["total"], 9)
        req = m.call_args[0][0]
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertIn("flk.npc.gov.cn", req.get_header("Referer"))
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["searchContent"], "行政处罚法")
        self.assertEqual(body["sxx"], [3])  # sxx 字符串转数组
        self.assertEqual(body["searchRange"], 1)

    def test_network_error_propagates(self):
        with mock.patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("net down")
        ):
            with self.assertRaises(urllib.error.URLError):
                qflk.fetch_list({"q": "x"})


class TestMain(unittest.TestCase):
    def setUp(self):
        scrub()

    def tearDown(self):
        scrub()

    def test_missing_q_returns_1(self):
        self.assertEqual(qflk.main([]), 1)

    def test_network_fail_writes_fallback(self):
        with mock.patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("down")
        ):
            code = qflk.main(["--q", "行政处罚法", "--out", SCRATCH_OUT])
        self.assertEqual(code, 1)
        with open(SCRATCH_OUT, encoding="utf-8") as f:
            payload = json.load(f)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["fallback"], "use WebSearch")

    def test_success_writes_items(self):
        payload = load_fixture()
        with mock.patch("urllib.request.urlopen", return_value=FakeResp(payload)):
            code = qflk.main(["--q", "行政处罚法", "--sxx", "3", "--out", SCRATCH_OUT])
        self.assertEqual(code, 0)
        with open(SCRATCH_OUT, encoding="utf-8") as f:
            result = json.load(f)
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 9)
        self.assertEqual(len(result["items"]), 3)
        self.assertEqual(result["query"]["sxx"], "3")

    def test_detail_mode(self):
        detail = {"code": 200, "data": {
            "bbbs": "x1", "title": "中华人民共和国行政处罚法", "flxz": "法律",
            "zdjgName": "全国人民代表大会", "gbrq": "2021-01-22", "sxrq": "2021-07-15",
            "sxx": 3, "xgwj": [], "ossFile": {"ossWordPath": "prod/x.docx"}}}
        with mock.patch("urllib.request.urlopen", return_value=FakeResp(detail)):
            code = qflk.main(["--detail", "x1", "--out", SCRATCH_OUT])
        self.assertEqual(code, 0)
        with open(SCRATCH_OUT, encoding="utf-8") as f:
            result = json.load(f)
        self.assertTrue(result["ok"])
        self.assertEqual(result["items"]["status_label"], "现行有效")
        self.assertEqual(result["items"]["xgwj_count"], 0)


if __name__ == "__main__":
    unittest.main()
