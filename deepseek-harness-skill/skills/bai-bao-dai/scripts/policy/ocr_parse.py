#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ocr_parse.py — 扫描公文 OCR/文档解析积木（惰性依赖 paddlepaddle + paddleocr）。

定位：百宝袋 policy 工具族模块。针对「无文本层的扫描红头件/报表」：
    text/pdf_parse.py 读不动的文件，先走本模块 OCR。

依赖（安装体积大）：
    pip install paddlepaddle paddleocr
    （用 PP-StructureV3/官方 doc-parsing 能力；接口随 PaddleOCR 3.x 演进，
     适配层按候选 API 顺序尝试，失败输出官方指引）

单独使用：
    python ocr_parse.py <图片或PDF路径> [输出.json]
作为库：
    from ocr_parse import parse_document, engine_ok
"""
import json
import os
import sys


def _ensure_cache_env():
    """PaddleOCR/PaddleX 首次导入会在 ~/.paddlex 建缓存。
    本模块默认把缓存重定向到 <当前目录>/.cache/paddlex（沙箱/便携友好），
    也可用环境变量 PADDLE_PDX_CACHE_HOME 覆盖。"""
    if not os.environ.get("PADDLE_PDX_CACHE_HOME"):
        cache = os.path.join(os.getcwd(), ".cache", "paddlex")
        try:
            os.makedirs(cache, exist_ok=True)
            os.environ["PADDLE_PDX_CACHE_HOME"] = cache
        except OSError:
            pass
    if not os.environ.get("HOME"):
        home = os.path.join(os.getcwd(), ".cache", "home")
        try:
            os.makedirs(home, exist_ok=True)
            os.environ["HOME"] = home
        except OSError:
            pass


_ensure_cache_env()


def _load_engine():
    """返回可用引擎调用器。依次尝试 PPStructureV3 → PaddleOCR(2.x 兼容) → 给出指引。"""
    try:
        import paddleocr  # noqa: F401
    except ImportError:
        sys.stderr.write("[ocr_parse] 缺少 paddleocr，安装: pip install paddlepaddle paddleocr\n")
        raise SystemExit(2)
    try:
        from paddleocr import PPStructureV3  # PaddleOCR 3.x 新结构
        return ("ppstructure-v3", lambda p: PPStructureV3(lang="ch").predict(p))
    except Exception:
        pass
    try:
        from paddleocr import PaddleOCR  # 2.x 兼容
        ocr = PaddleOCR(use_doc_orientation_classify=True, use_doc_unwarping=True,
                        use_textline_orientation=True, lang="ch")
        return ("paddleocr-2x", lambda p: ocr.predict(p) if hasattr(ocr, "predict") else ocr.ocr(p))
    except Exception:
        sys.stderr.write("[ocr_parse] 未能初始化 PaddleOCR 引擎；请按官方 paddleocr-doc-parsing 指引\n")
        raise SystemExit(3)


def engine_ok():
    """环境预检：paddle / paddleocr 是否可导入。"""
    try:
        import paddle  # noqa: F401
        import paddleocr  # noqa: F401
        return True
    except Exception:
        return False


def parse_document(path):
    name, call = _load_engine()
    raw = call(path)
    # raw 结构随版本而异：尽量规整为 [{page/index, text?, ...}] 的浅结构
    if isinstance(raw, list):
        out = []
        for it in raw:
            if hasattr(it, "__dict__"):
                it = it.__dict__
            if isinstance(it, dict):
                text = (it.get("rec_texts") or it.get("texts")
                        or it.get("res") or it.get("text"))
                out.append({"type": it.get("type"), "text": text})
            else:
                out.append({"raw": str(it)[:500]})
        return {"engine": name, "blocks": out}
    return {"engine": name, "raw": str(raw)[:2000]}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("用法: python ocr_parse.py <图片或PDF路径> [输出.json]；python ocr_parse.py --check",
              file=sys.stderr)
        return 1
    if argv[0] == "--check":
        ok = engine_ok()
        print(json.dumps({"engine_ready": ok}, ensure_ascii=False, indent=2))
        return 0 if ok else 2
    path, out_path = argv[0], (argv[1] if len(argv) > 1 else None)
    try:
        result = parse_document(path)
        result["ok"] = True
    except SystemExit as e:
        return int(e.code or 1)
    except Exception as e:  # noqa: BLE001
        print(f"OCR 失败: {e}", file=sys.stderr)
        return 1
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"ok -> {out_path}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
