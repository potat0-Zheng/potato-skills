# -*- coding: utf-8 -*-
"""回归测试的公共工具：可写临时目录、示例语料复制、无管道的 CLI 调用。

为什么不用 `tempfile.mkdtemp()` 的默认目录：本沙箱（Windows/DSH）下它的默认目录**不可写**
（重定向 TMP/TEMP 也无效），测试会以 PermissionError 全灭。故一律建在 `tests/_tmp/` 下
（先 `os.makedirs`），退出时统一清理。
"""
import atexit
import io
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
SCRIPTS = os.path.join(SKILL_DIR, "scripts")
EXAMPLE = os.path.join(SKILL_DIR, "data", "example")
TMP_ROOT = os.path.join(HERE, "_tmp")

if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

atexit.register(lambda: shutil.rmtree(TMP_ROOT, ignore_errors=True))


def workdir(name):
    """给某个测试模块一个干净的、**可写**的工作目录。"""
    d = os.path.join(TMP_ROOT, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    return d


def copy_example(dst, drop_ids=()):
    """把示例语料复制到工作目录（这样测试不会写脏 skill 自带的数据）。

    drop_ids：需要从语料里删掉的记录 id（用于构造"池与 residual 不同源"这类情形）。
    """
    os.makedirs(os.path.join(dst, "normalized"), exist_ok=True)
    src = os.path.join(EXAMPLE, "normalized", "data.jsonl")
    rows = []
    with io.open(src, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and json.loads(line).get("id") not in set(drop_ids):
                rows.append(line)
    with io.open(os.path.join(dst, "normalized", "data.jsonl"), "w", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")
    for name in ("stances.json", "opinion_rules.json", "events.json", "llm_labels.jsonl"):
        shutil.copy(os.path.join(EXAMPLE, name), os.path.join(dst, name))
    return dst


def corpus(dst):
    return os.path.join(dst, "normalized", "data.jsonl")


def run(script, args, log_dir=None):
    """跑脚本并返回退出码。

    **不用管道捕获 stdout/stderr**（本沙箱下管道会 EPERM）：要么丢弃，要么重定向到文件，
    需要看输出就读文件——这也顺便留下排障日志。
    """
    cmd = [sys.executable, os.path.join(SCRIPTS, script)] + [str(a) for a in args]
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        with io.open(os.path.join(log_dir, "out.txt"), "w", encoding="utf-8") as o, \
                io.open(os.path.join(log_dir, "err.txt"), "w", encoding="utf-8") as e:
            return subprocess.run(cmd, stdout=o, stderr=e).returncode
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode


def load(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, obj):
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def make_opinion(dst, out_name="opinion.json", extra=(), event="回归测试"):
    """跑一遍 opinion.py（默认带示例的词表/规则/事件/LLM 标注）。返回工件路径。"""
    out = os.path.join(dst, out_name)
    args = ["--in", corpus(dst), "--out", out, "--event", event,
            "--stances", os.path.join(dst, "stances.json")]
    args += list(extra)
    rc = run("opinion.py", args, log_dir=dst)
    if rc != 0:
        raise AssertionError("opinion.py 退出码 %s（见 %s/err.txt）" % (rc, dst))
    return out
