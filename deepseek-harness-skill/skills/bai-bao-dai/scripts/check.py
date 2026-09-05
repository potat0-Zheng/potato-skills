# -*- coding: utf-8 -*-
"""bai-bao-dai 事实核查辅助。

将事件关键事实断言标准化为核查记录，并尝试调用 china_sources（po-xu-wang 工具）做初步核查。
输出 facts.json（断言 + 结论 + 证据），供 build_report 渲染与人工/LLM 复核定夺。

用法：
  python check.py --event "事件关键词" --claims "断言1","断言2" --out data/demo/facts.json
  python check.py --event "x" --file claims.json --out data/demo/facts.json   # claims.json 为 ["断言1","断言2",...]
"""
import argparse
import json
import os
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 尝试加载 china_sources（可选，依赖 po-xu-wang skill 工具）
# 定位：优先取环境变量 CHINA_SOURCES_DIR；否则在同级 skills 目录下找 po-xu-wang
_CHINA_CANDIDATES = [
    os.environ.get("CHINA_SOURCES_DIR", ""),
    os.path.join(os.path.dirname(SKILL_DIR), "po-xu-wang", "tools"),
]
try:
    _client = None
    HAS_CHINA = False
    for d in _CHINA_CANDIDATES:
        if not d or not os.path.isdir(d):
            continue
        if os.path.exists(os.path.join(d, "china_sources.py")):
            sys.path.insert(0, d)
            from china_sources import ChinaVerifyClient
            _client = ChinaVerifyClient(timeout=10, retries=0)
            HAS_CHINA = True
            break
except Exception:
    _client = None
    HAS_CHINA = False


def verify_one(claim):
    rec = {"claim": claim, "verdict": "待核查", "reason": "", "evidence": [], "type": "general"}
    if not HAS_CHINA:
        rec["reason"] = "china_sources 不可用（po-xu-wang skill 未安装或路径不可达），待 LLM 多源核查"
        return rec
    try:
        r = _client.verify(claim, claim_type="general")
        finds = r.get("findings", {})
        p = finds.get("piyao", {})
        if p.get("found"):
            rec["evidence"].append({"source": "piyao.org.cn", "items": p.get("results", [])[:5]})
        t = finds.get("thepaper", {})
        if t.get("results"):
            rec["evidence"].append({"source": "thepaper.cn", "items": t.get("results", [])[:5]})
        w = finds.get("weibo", {})
        if w.get("results"):
            rec["evidence"].append({"source": "weibo.com", "items": w.get("results", [])[:5]})
        if rec["evidence"]:
            rec["reason"] = "china_sources 初查命中 %d 类证据源（见 evidence）；最终 verdict 需 LLM 结合多源定夺" % len(rec["evidence"])
        else:
            rec["reason"] = "china_sources 初查未返回有效证据（部分上游接口可能失效，见日志），待 LLM 多源核查"
    except Exception as e:
        rec["reason"] = "核查调用异常：%s" % e
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True)
    ap.add_argument("--claims", default="", help="逗号分隔的断言列表")
    ap.add_argument("--file", default="", help="断言 JSON 文件（字符串数组）")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    claims = []
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            claims = json.load(f)
    if args.claims:
        claims += [c.strip() for c in args.claims.split(",") if c.strip()]

    facts = {"event": args.event, "claims": [], "meta": {"china_sources": HAS_CHINA}}
    if not claims:
        print("[check] 警告：未提供任何断言（--claims 或 --file 为空），facts.json 将不含核查记录")
    for c in claims:
        facts["claims"].append(verify_one(c))

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False, indent=2)
    print("[check] 核查 %d 条断言（china_sources=%s）→ %s" % (len(claims), HAS_CHINA, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
