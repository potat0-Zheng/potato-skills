# -*- coding: utf-8 -*-
"""
揽风云 辅助脚本 — 结构化数据提取（extract_stats）

从中文/英文文本中提取「数字 + 单位 + 上下文」的结构化片段，
供信息汇编报告引用与复核，减少模型手工提取数字的遗漏与错误。

用法：
    python extract_stats.py <输入文件> [--output stats.json] [--context 40]

输入：UTF-8 文本文件（抓取正文或搜索摘要，建议先写入文件再执行）。
输出：JSON 数组，每项含：
    { "text": 完整匹配串, "value": 规范化数值, "unit": 单位,
      "context": 前后文, "offset": 在原文中的字符偏移 }

依赖：纯标准库（re / json / sys / argparse）。

DSH/Windows 契约：
    中文输出一律写 UTF-8 文件后 read；执行前设
    $env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
"""

import re
import json
import sys
import argparse

# ── 正则模式 ───────────────────────────────────────────────
# 数字：12,345.6 或 123.4 或 123
_NUM = r"\d{1,3}(?:,\d{3})*\.?\d*|\d+\.?\d*"
# 百分比：45.2% / 45 %
_PCT = re.compile(rf"({_NUM})\s*%")

# 中文单位/量词（按长度降序排列，优先匹配长单位避免截断）
_CN_UNITS = [
    "万亿元", "亿元", "万元", "美元", "欧元", "日元", "人民币",
    "平方米", "平方公里", "立方米", "公里", "千米", "公斤", "千克",
    "百分点", "人次", "病例", "死亡", "遇难", "受伤", "确诊",
    "人", "名", "户", "辆", "架", "艘", "起", "次", "家", "项",
    "例", "岁", "层", "元", "天", "小时", "分钟", "秒", "米", "吨",
]
_CN_UNIT_ALT = "|".join(sorted(_CN_UNITS, key=len, reverse=True))
# 数字 + 中文单位（中间允许空格）
_NUM_CN_UNIT = re.compile(rf"({_NUM})\s*({_CN_UNIT_ALT})")

# 日期：2026年5月13日 / 2026年5月 / 2026-05-13 / 5月13日
_DATE_FULL = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?")
_DATE_ISO = re.compile(r"\d{4}-\d{1,2}-\d{1,2}")
_DATE_SHORT = re.compile(r"(?<!\d)\d{1,2}\s*月\s*\d{1,2}\s*日(?!\d)")
# 时间：11:30 / 11:30:05
_TIME = re.compile(rf"({_NUM}):\d{{2}}(?::\d{{2}})?")

# 纯数字（不归属以上任何类别时兜底，排除日期里的数字）
_NUM_BARE = re.compile(rf"(?<!\w)({_NUM})(?!\w|%|\s*(?:{_CN_UNIT_ALT}))")


def _normalize_value(raw: str) -> float | int:
    """去掉千分位逗号，转数值（优先 int，其次 float）。"""
    s = raw.replace(",", "")
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return raw


def _collect(patterns: list, text: str, context_len: int) -> list:
    """跑所有模式，合并为按 offset 排序、区间去重的条目列表。

    patterns 元素为 (compiled_pattern, extractor)，extractor(m) 返回
    (raw_value_str, unit)。按 patterns 顺序处理（高优先级在前）；
    与已接受条目「区间重叠」的匹配被跳过（如日期内部的裸数字）。
    """
    accepted = []  # [(start, end, item)]
    for pat, extractor in patterns:
        for m in pat.finditer(text):
            s, e = m.start(), m.end()
            if any(s < ae and e > as_ for as_, ae, _ in accepted):
                continue  # 与更高优先级条目区间重叠 → 跳过
            raw, unit = extractor(m)
            ctx_start = max(0, s - context_len)
            ctx_end = min(len(text), e + context_len)
            item = {
                "text": m.group(0),
                "value": _normalize_value(raw),
                "unit": unit,
                "context": re.sub(r"\s+", " ", text[ctx_start:ctx_end]).strip(),
                "offset": s,
            }
            accepted.append((s, e, item))
    items = sorted((it for _, _, it in accepted), key=lambda x: x["offset"])
    return items


def extract_stats(text: str, context_len: int = 40) -> list:
    """主函数：从文本提取全部数字事实条目。"""
    patterns = [
        (_PCT, lambda m: (m.group(1), "%")),
        (_DATE_FULL, lambda m: (m.group(0), "日期")),
        (_DATE_ISO, lambda m: (m.group(0), "日期")),
        (_DATE_SHORT, lambda m: (m.group(0), "日期")),
        (_TIME, lambda m: (m.group(1), "时间")),
        (_NUM_CN_UNIT, lambda m: (m.group(1), m.group(2))),
        (_NUM_BARE, lambda m: (m.group(1), "")),
    ]
    return _collect(patterns, text, context_len)


# ── CLI ───────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="揽风云 — 结构化数据提取")
    ap.add_argument("input", help="输入文件路径（UTF-8）")
    ap.add_argument("-o", "--output", help="输出 JSON 文件路径（默认 stdout）")
    ap.add_argument("-c", "--context", type=int, default=40, help="上下文窗口字符数（默认 40）")
    args = ap.parse_args()

    try:
        with open(args.input, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print(f"[错误] 无法读取输入文件: {e}", file=sys.stderr)
        sys.exit(1)

    items = extract_stats(text, args.context)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(items, ensure_ascii=False, indent=2, fp=f)
        print(f"已提取 {len(items)} 条 → {args.output}")
    else:
        print(json.dumps(items, ensure_ascii=False, indent=2))

    # 统计到 stderr
    units = {}
    for it in items:
        u = it["unit"] or "裸数字"
        units[u] = units.get(u, 0) + 1
    top = sorted(units.items(), key=lambda kv: -kv[1])[:8]
    print("[统计] " + " · ".join(f"{k}×{v}" for k, v in top), file=sys.stderr)


if __name__ == "__main__":
    main()
