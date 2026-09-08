#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pdf_parse.py — 政务 PDF 解析积木（文本层/表格；惰性依赖 pdfplumber/camelot）。

定位：百宝袋 policy 工具族模块。可单独使用，也可被其他模块 import 拼接。

依赖（惰性，仅调用相关子命令时才 import）：
    pip install pdfplumber "camelot-py[base]"

单独使用：
    python pdf_parse.py text   报告.pdf [输出.txt]     # 提取文本层文字
    python pdf_parse.py tables 公报.pdf [输出.json]    # 提取表格（规整框线）
    python pdf_parse.py info   报告.pdf                # 页数/是否含文本层
作为库：
    from pdf_parse import extract_text, extract_tables, pdf_info
说明：扫描件（无文本层）请先走 ocr_parse.py。
"""
import json
import sys

HINTS = {
    "pdfplumber": "pip install pdfplumber",
    "camelot": 'pip install "camelot-py[base]"',
}


def _need(module, hint):
    try:
        return __import__(module)
    except ImportError:
        sys.stderr.write(f"[pdf_parse] 缺少依赖 {module}，安装: {hint}\n")
        raise SystemExit(2)


def extract_text(path):
    pdfplumber = _need("pdfplumber", HINTS["pdfplumber"])
    parts = []
    with pdfplumber.open(path) as pdf:
        for p in pdf.pages:
            parts.append(p.extract_text() or "")
    return "\n".join(parts)


def extract_tables(path):
    pdfplumber = _need("pdfplumber", HINTS["pdfplumber"])
    out = []
    with pdfplumber.open(path) as pdf:
        for i, p in enumerate(pdf.pages):
            for t in p.extract_tables() or []:
                out.append({"page": i + 1, "rows": [[c if c is not None else "" for c in r] for r in t]})
    return out


def extract_tables_camelot(path):
    """备用：Camelot 规则表格（需 Ghostscript 的场景按报错提示安装）。"""
    camelot = _need("camelot", HINTS["camelot"])
    tables = camelot.read_pdf(path, pages="all")
    return [{"page": t.page, "rows": t.data} for t in tables]


def pdf_info(path):
    pdfplumber = _need("pdfplumber", HINTS["pdfplumber"])
    with pdfplumber.open(path) as pdf:
        has_text = any(bool((p.extract_text() or "").strip()) for p in pdf.pages[:5])
        return {"pages": len(pdf.pages), "has_text_layer": has_text}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print("用法: python pdf_parse.py <text|tables|tables-camelot|info> <pdf路径> [输出路径]",
              file=sys.stderr)
        return 1
    mode, path = argv[0], argv[1]
    out_path = argv[2] if len(argv) > 2 else None
    try:
        if mode == "text":
            result = {"ok": True, "mode": "text", "content": extract_text(path)}
        elif mode == "tables":
            result = {"ok": True, "mode": "tables", "tables": extract_tables(path)}
        elif mode == "tables-camelot":
            result = {"ok": True, "mode": "tables-camelot", "tables": extract_tables_camelot(path)}
        elif mode == "info":
            result = {"ok": True, "mode": "info", **pdf_info(path)}
        else:
            print("未知模式: " + mode, file=sys.stderr)
            return 1
    except FileNotFoundError:
        print(f"文件不存在: {path}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"解析失败: {e}", file=sys.stderr)
        return 1
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"ok: {mode} -> {out_path}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
