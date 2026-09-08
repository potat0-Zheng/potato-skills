# -*- coding: utf-8 -*-
"""report-theme 同步脚本（方案 C：独立主模板 + 各 skill 本地自包含副本）。

用法（在 report-theme 目录下，或任意位置执行本脚本）：
    python sync_theme.py            # 同步：把主模板分发到各 skill 本地副本
    python sync_theme.py --check    # 检查：只报告各副本与主模板的一致性，不写入

原则：
- 主模板 = 单一事实源（本目录 report_template.html）
- 各 skill 运行时只读自己的本地副本 → 保持 skill 相对独立（主模板缺失不影响运行）
- 同步脚本负责收敛副本、防止漂移；副本头部带 theme-v 版本标记
"""
import argparse
import hashlib
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # ~/.dsh
MASTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_template.html")

# 各 skill 的本地模板路径（相对 skills/<name>/）
TARGETS = [
    ("bai-bao-dai", "templates/report_template.html"),
    ("lan-feng-yun", "assets/report_template.html"),
    # 未来纳入：can-guo-shi（当前按引用继承揽风云模板，无本地副本）、
    #           po-xu-wang（当前无 HTML 输出；若新增 HTML 核查报告模式可加入）
]


def sha1(p):
    h = hashlib.sha1()
    with open(p, "rb") as f:
        h.update(f.read())
    return h.hexdigest()[:12]


def theme_version(text):
    m = re.search(r"theme-v:\s*([\w.\-]+)", text)
    return m.group(1) if m else "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查一致性，不写入")
    args = ap.parse_args()

    if not os.path.exists(MASTER):
        print("[错误] 主模板缺失:", MASTER)
        return 1
    with open(MASTER, "r", encoding="utf-8") as f:
        master_text = f.read()
    master_sha = sha1(MASTER)
    ver = theme_version(master_text)
    print(f"[主模板] {MASTER}  v{ver}  sha1={master_sha}")

    ok = True
    for name, rel in TARGETS:
        target = os.path.join(BASE, "skills", name, rel)
        if not os.path.exists(target):
            if args.check:
                print(f"[{name}] MISSING  {rel}")
            else:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "w", encoding="utf-8") as f:
                    f.write(master_text)
                print(f"[{name}] CREATED  {rel}  v{ver}")
            ok = False
            continue
        with open(target, "r", encoding="utf-8") as f:
            target_text = f.read()
        t_sha = sha1(target)
        t_ver = theme_version(target_text)
        if target_text == master_text:
            print(f"[{name}] IN_SYNC  {rel}  v{t_ver}")
        else:
            print(f"[{name}] DRIFTED  {rel}  本地 v{t_ver} ≠ 主模板 v{ver}  (sha1 {t_sha} vs {master_sha})")
            if not args.check:
                with open(target, "w", encoding="utf-8") as f:
                    f.write(master_text)
                print(f"[{name}] UPDATED  {rel}  → v{ver}")
            ok = False

    print("-" * 50)
    if args.check:
        print("检查完成：" + ("全部一致" if ok else "存在漂移/缺失，请运行 python sync_theme.py 同步"))
        return 0 if ok else 1
    print("同步完成。各 skill 运行时只读本地副本（保持相对独立）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
