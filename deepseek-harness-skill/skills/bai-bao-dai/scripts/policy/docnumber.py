#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""docnumber.py — 文号批量解析模块（纯标准库，零依赖）。

定位：百宝袋 policy 工具族的「积木」之一。可单独使用，也可被其他模块/流水线 import。

单独使用：
    python docnumber.py "国务院办公厅关于印发X的通知（国办发〔2026〕12号）" "生态环境部令第32号"
    python docnumber.py -f 文件.txt                 # 读入文本文件再解析
    python docnumber.py --json out.json "国发〔2026〕5号"

作为库使用：
    from docnumber import parse_docnumbers
    parse_docnumbers("...国办发〔2026〕12号...")     # -> [{"文号", "年份", "序号", "前缀", "发布主体", "类型", "联合发文"}]

设计原则（继承参国是「扩展工具」设计原则）：
    - 零依赖、单文件、纯正则；输出 JSON（UTF-8）
    - 发布主体=仅基于文号前缀规则的粗映射；无法映射者置 None 并保留原始前缀，交由上层/LLM 处理
    - 不做语义级推断
"""
import json
import re
import sys

# ── 文号正则 ──────────────────────────────────────────────
# 形如：国发〔2026〕5号 / 国办发〔2026〕12号 / X政发〔2026〕8号 / 〔〕或半角括号
RE_BRACKET = re.compile(r"([\u4e00-\u9fa5A-Za-z]{2,20}?)(?:〔|\[)(\d{4})(?:〕|\])第?(\d+)号")
# 形如：生态环境部令第32号 / 国务院令第700号
RE_LING = re.compile(r"([\u4e00-\u9fa5A-Za-z]{2,20}?令)第(\d+)号")

# ── 前缀 → (发布主体, 类型) 粗映射（规则级，非语义）──────
PREFIX_MAP = {
    "国发": ("国务院", "发"), "国函": ("国务院", "函"),
    "国办发": ("国务院办公厅", "发"), "国办函": ("国务院办公厅", "函"),
    "中发": ("中共中央", "发"), "中办发": ("中共中央办公厅", "发"),
}
M_INST = ("发改委", "工信", "教育", "科技", "公安", "民政", "财政", "人社", "自然资源",
          "生态", "住建", "交通", "水利", "农业", "商务", "文化和旅游", "卫健", "应急",
          "人民银行", "审计", "国资委", "海关总署", "税务总局", "市场监管", "广电",
          "体育", "统计", "医保", "气象", "银保监", "证监", "能源")


def _infer(body, prefix, is_ling):
    """依据前缀做规则级发布主体/类型推断。无法判断返回 (None, 类型, False)。"""
    if body:
        return body, "令" if is_ling else None, False
    for key, (pub, typ) in PREFIX_MAP.items():
        if prefix.startswith(key):
            return pub, typ, False
    if prefix.startswith("国"):
        return "国务院序列（待精确）", None, False
    if re.search(r"政(台|办)?发$", prefix):
        return "省级政府部门（待精确）", "发", False
    for inst in M_INST:
        if prefix.startswith(inst):
            return inst + "（待精确）", None, False
    return None, None, False


def parse_docnumbers(text):
    """从文本中提取所有文号。返回结构化列表（去重保序）。"""
    seen, out = set(), []
    for m in RE_BRACKET.finditer(text or ""):
        prefix = m.group(1)
        prefix = re.sub(r"(关于印发|印发|关于|转发|的|通知)$", "", prefix)
        prefix = prefix.strip(" ，,。;；")
        year, seq = int(m.group(2)), int(m.group(3))
        joined = "联" in prefix
        pub, typ, _ = _infer(None, prefix, False)
        key = ("b", prefix, year, seq)
        if key not in seen:
            seen.add(key)
            out.append({
                "文号": f"{prefix}〔{year}〕{seq}号",
                "前缀": prefix, "年份": year, "序号": seq,
                "发布主体": pub,
                "类型": typ or ("联" if joined else None),
                "联合发文": joined,
            })
    for m in RE_LING.finditer(text or ""):
        body = m.group(1).rstrip("令")
        seq = int(m.group(2))
        key = ("l", body, seq)
        if key not in seen:
            seen.add(key)
            pub, typ, _ = _infer(body, "", True)
            out.append({"文号": f"{body}令第{seq}号", "前缀": body,
                        "年份": None, "序号": seq, "发布主体": pub,
                        "类型": "令", "联合发文": False})
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    chunks, out_path = [], None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-f", "--file"):
            with open(argv[i + 1], encoding="utf-8") as f:
                chunks.append(f.read())
            i += 2
        elif a == "--json":
            out_path = argv[i + 1]
            i += 2
        else:
            chunks.append(a)
            i += 1
    if not chunks:
        print("用法: python docnumber.py <文本…> 或 -f 文件.txt [--json 输出.json]",
              file=sys.stderr)
        return 1
    result = parse_docnumbers(" ".join(chunks))
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"ok: 解析出 {len(result)} 条文号 -> {out_path}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
