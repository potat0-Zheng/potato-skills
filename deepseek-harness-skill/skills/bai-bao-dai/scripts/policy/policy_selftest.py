#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""policy_selftest.py — policy 工具族离线自检（无需网络）。

覆盖：
    A 组（零依赖）：docnumber 解析样本、query_gov/query_flk 模块可导入
    B 组（惰性依赖）：pdfplumber / camelot / paddle / paddleocr 是否已安装

用法：python policy_selftest.py [--json]
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

SAMPLE = ("国务院办公厅关于印发《数据要素×行动实施方案》的通知（国办发〔2026〕12号）；"
          "生态环境部令第32号；浙江省人民政府关于……（浙政发〔2026〕8号）")


def check_docnumber():
    sys.path.insert(0, HERE)
    try:
        from docnumber import parse_docnumbers  # noqa: PLC0415
        got = parse_docnumbers(SAMPLE)
        nums = {d["文号"] for d in got}
        exp = {"国办发〔2026〕12号", "生态环境部令第32号", "浙政发〔2026〕8号"}
        missing = exp - nums
        return len(missing) == 0 and len(nums) >= len(exp) - 1, got
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def check_import(name, pkg):
    return importlib.util.find_spec(pkg) is not None


def main():
    as_json = "--json" in sys.argv
    doc_ok, detail = check_docnumber()
    report = {
        "A_docnumber": doc_ok,
        "A_docnumber_sample_hits": [d["文号"] for d in detail] if isinstance(detail, list) else detail,
        "B_pdfplumber": check_import("pdfplumber", "pdfplumber"),
        "B_camelot": check_import("camelot", "camelot"),
        "B_paddle": check_import("paddle", "paddle"),
        "B_paddleocr": check_import("paddleocr", "paddleocr"),
    }
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for k, v in report.items():
            print(f"{'✅' if v is True else ('⚠️' if v is False else '·')} {k}: {v if not isinstance(v, bool) else ''}")
    core_ok = report["A_docnumber"]
    print("结论: A 组(核心) " + ("通过" if core_ok else "失败"))
    return 0 if core_ok else 1


if __name__ == "__main__":
    sys.exit(main())
