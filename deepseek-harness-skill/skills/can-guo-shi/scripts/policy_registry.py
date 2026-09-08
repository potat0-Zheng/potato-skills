#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""policy_registry — 参国是跨会话政策注册表操作脚本（纯标准库，零依赖）。

registry 定位：已覆盖政策条目的索引（非事实库、非完整档案）。
默认存储：~/.dsh/skills/can-guo-shi/registry/{policies.json, coverage_log.json}
可用 --root DIR 覆盖（便于测试与便携部署）。

用法：
  python policy_registry.py add    --entries entries.json [--root DIR]
  python policy_registry.py list   [--domain X] [--region X] [--since YYYY-MM] [--root DIR]
  python policy_registry.py stats  [--root DIR]

去重键：文号（有则唯一）；无文号用 名称+发布机构+发布日期 三元组。
add 采用原子写（临时文件 + os.replace），重复项跳过并报告。
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import date

ROOT_DEFAULT = os.path.join(os.path.expanduser("~"), ".dsh", "skills", "can-guo-shi", "registry")
POLICIES = "policies.json"
COVERAGE = "coverage_log.json"

def norm_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    return list(v)

def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise

def entry_key(e):
    doc = (e.get("文号") or "").strip()
    if doc:
        return ("doc", doc)
    return ("triple", (e.get("名称") or "").strip(),
            (e.get("发布机构") or "").strip(),
            (e.get("发布日期") or "").strip())

def load_root(args):
    root = getattr(args, "root", None) or os.environ.get("CGS_REGISTRY_ROOT") or ROOT_DEFAULT
    return root, os.path.join(root, POLICIES), os.path.join(root, COVERAGE)

def do_add(args):
    root, p_path, c_path = load_root(args)
    existing = load_json(p_path) or []
    seen = {}
    for e in existing:
        seen[entry_key(e)] = True
    with open(args.entries, "r", encoding="utf-8") as f:
        incoming = json.load(f)
    if isinstance(incoming, dict):
        incoming = [incoming]
    added, dups = [], []
    for e in incoming:
        if not isinstance(e, dict) or not (e.get("名称") or "").strip():
            dups.append(("INVALID", e))
            continue
        e.setdefault("收录时间", date.today().isoformat())
        k = entry_key(e)
        if k in seen:
            dups.append(e)
        else:
            seen[k] = True
            added.append(e)
    if added:
        save_json_atomic(p_path, existing + added)
    if args.log and added:
        prev = load_json(c_path) or []
        doms = sorted({d for e in added for d in norm_list(e.get("领域"))})
        regs = sorted({r for e in added for r in norm_list(e.get("地域"))})
        prev.append({
            "收录时间": date.today().isoformat(),
            "月份": date.today().strftime("%Y-%m"),
            "领域": doms,
            "地域": regs,
            "新增条数": len(added),
            "重复条数": len(dups),
        })
        save_json_atomic(c_path, prev)
    print("新增 %d 条，重复 %d 条" % (len(added), len(dups)))
    for e in added:
        print("  + %s | %s | %s | %s | %s" % (
            e.get("发布日期", "?"), e.get("效力层级", "?"),
            e.get("名称", "?"), e.get("发布机构", "?"), e.get("文号", "")))
    for e in dups:
        name = e.get("名称") if isinstance(e, dict) else e
        print("  = 重复跳过: %s" % (name if name != "INVALID" else "无效条目"))

def _match(e, domain, region, since):
    if domain:
        if not any(domain in d for d in norm_list(e.get("领域"))):
            return False
    if region:
        if not any(region in r for r in norm_list(e.get("地域"))):
            return False
    if since:
        pub = (e.get("发布日期") or "").strip()
        if pub and len(pub) >= 7 and pub[:7] < since:
            return False
    return True

def do_list(args):
    _, p_path, _ = load_root(args)
    items = load_json(p_path) or []
    hits = [e for e in items if _match(e, args.domain, args.region, args.since)]
    hits.sort(key=lambda e: (e.get("发布日期") or "9999"))
    print("共 %d 条（匹配过滤）" % len(hits))
    for e in hits:
        print("%s | [%s] | %s | %s | %s | %s" % (
            e.get("发布日期", "?"), e.get("效力层级", "?"), e.get("名称", "?"),
            e.get("发布机构", "?"), e.get("文号", "") or "-",
            e.get("时效状态", "?")))

def do_stats(args):
    _, p_path, _ = load_root(args)
    items = load_json(p_path) or []
    by_domain, by_region, by_month, by_status = {}, {}, {}, {}
    for e in items:
        for d in norm_list(e.get("领域")):
            by_domain[d] = by_domain.get(d, 0) + 1
        for r in norm_list(e.get("地域")):
            by_region[r] = by_region.get(r, 0) + 1
        pub = (e.get("发布日期") or "").strip()
        m = pub[:7] if pub else "?"
        by_month[m] = by_month.get(m, 0) + 1
        st = e.get("时效状态") or "待核验"
        by_status[st] = by_status.get(st, 0) + 1
    print("总条目：%d" % len(items))
    print("按领域：%s" % _fmt(by_domain))
    print("按地域：%s" % _fmt(by_region))
    print("按发布日期月份：%s" % _fmt(by_month))
    print("按时效状态：%s" % _fmt(by_status))

def _fmt(d):
    return ", ".join("%s=%d" % (k, v) for k, v in sorted(d.items(), key=lambda kv: -kv[1]))

def main():
    ap = argparse.ArgumentParser(description="参国是跨会话政策注册表")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add", help="批量收录条目")
    p_add.add_argument("--entries", required=True, help="entries JSON 文件路径")
    p_add.add_argument("--no-log", action="store_true", help="不回写 coverage_log")
    p_add.add_argument("--root")
    p_add.set_defaults(fn=do_add)
    p_list = sub.add_parser("list", help="列出条目（可过滤）")
    p_list.add_argument("--domain")
    p_list.add_argument("--region")
    p_list.add_argument("--since", metavar="YYYY-MM")
    p_list.add_argument("--root")
    p_list.set_defaults(fn=do_list)
    p_stats = sub.add_parser("stats", help="统计")
    p_stats.add_argument("--root")
    p_stats.set_defaults(fn=do_stats)
    args = ap.parse_args()
    args.log = not getattr(args, "no_log", False)
    args.fn(args)

if __name__ == "__main__":
    main()
