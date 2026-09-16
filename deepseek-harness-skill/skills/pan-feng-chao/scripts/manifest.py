# -*- coding: utf-8 -*-
"""pan-feng-chao 工件指纹（run_manifest）：让"这份数字怎么来的"可机器核对。

设计要点：
  · **不写时间戳**：时间会让同一输入产生不同工件，破坏"同输入同输出"的可复现纪律。
    需要时间就由调用方在外部记录，指纹只负责"内容与参数"。
  · 只记录**输入指纹 + 参数 + 词表指纹 + 脚本指纹**，不复制内容，故体积很小（<2 KB）。
  · 纯标准库（hashlib/json/os），零第三方依赖。
  · 大文件读全量哈希（万级语料毫秒级），换取确定性；不使用"抽样哈希"这种不确定做法。
"""
import hashlib
import json
import os

CHUNK = 1 << 20

# 路径类参数：其内容由 inputs / vocabulary 指纹覆盖，不参与"参数变化"比对
PATH_ARGS = {"src", "out", "prev", "stances", "institutional", "rules",
             "llm_labels", "llm_labels_b", "events", "digest", "md"}


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _count_lines(path):
    n = 0
    with open(path, "rb") as f:
        for _ in f:
            n += 1
    return n


def file_fingerprint(path, with_lines=False):
    """单个输入文件的指纹：**文件名** + sha256 + 字节数（可选行数）。文件不存在时返回 None。

    v0.7 起**不再记录绝对路径**（原本是 `"path": str(path)`）。两个理由：
      ① 指纹的用途是"内容有没有变"，绝对路径对它没有信息量；
      ② 绝对路径会把**本机用户名与目录结构**写进 `opinion.json`，而该工件会随报告与发行包外流
         （实测形如 `C:\\Users\\<用户名>\\...`）。名字足够回答"同一角色换的是哪个文件"。
    """
    if not path or not os.path.exists(path):
        return None
    fp = {"name": os.path.basename(str(path)),
          "sha256": _sha256_file(path), "bytes": os.path.getsize(path)}
    if with_lines:
        try:
            fp["lines"] = _count_lines(path)
        except OSError:
            pass
    return fp


def script_fingerprint(script_path):
    """脚本自身指纹：改过脚本就必须能看出来（否则"数字变了"无从归因）。"""
    fp = file_fingerprint(script_path)
    if not fp:
        return None
    try:
        with open(script_path, "r", encoding="utf-8") as f:
            fp["lines"] = sum(1 for _ in f)
    except OSError:
        pass
    fp["name"] = os.path.basename(script_path)
    fp.pop("path", None)
    return fp


def canonical_sha256(obj):
    """对任意 JSON 可序列化对象取**规范化**哈希（键排序、无空白差异）。"""
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def sanitize_args(args):
    """把 argparse 的 Namespace 变成"可比较"的参数字典：丢掉私有字段与路径类噪声。"""
    out = {}
    for k, v in sorted(vars(args).items()):
        if k.startswith("_"):
            continue
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, (list, tuple)):
            out[k] = list(v)
    return out


def build(inputs=None, args=None, vocabulary=None, script_path="", extra=None,
          reference=None):
    """组装 run_manifest。

    inputs     : [(路径, 是否统计行数[, 角色名])]；**角色名很重要**——它让"换了词表文件"
                 能报成「词表变化」而不是同时报「输入消失 + 输入新增」。
    args       : argparse.Namespace 或 dict
    vocabulary : dict（如 stances 配置）或文件路径
    reference  : 上游工件路径（如 spotcheck 引用它消费的 opinion.json）→ 记其 sha256
    """
    ins = []
    for item in (inputs or []):
        if isinstance(item, (list, tuple)):
            path = item[0]
            with_lines = item[1] if len(item) > 1 else False
            role = item[2] if len(item) > 2 else ""
        else:
            path, with_lines, role = item, False, ""
        fp = file_fingerprint(path, with_lines=bool(with_lines))
        if fp:
            fp["role"] = role or os.path.basename(str(path))
            ins.append(fp)
    vocab_fp = None
    if isinstance(vocabulary, str):
        vocab_fp = file_fingerprint(vocabulary)
    elif vocabulary is not None:
        vocab_fp = {"sha256": canonical_sha256(vocabulary)}
    if args is not None and not isinstance(args, dict):
        args = sanitize_args(args)
    # 路径类参数不参与"参数变化"比对：它们的内容已由 inputs/词表指纹覆盖，
    # 否则换一个词表文件会同时报「词表变化」和两条「参数变化」，噪声盖过信号。
    if args:
        args = {k: v for k, v in args.items() if k not in PATH_ARGS}
    man = {
        "inputs": ins,
        "args": args or {},
        "vocabulary_sha256": (vocab_fp or {}).get("sha256"),
        "script": script_fingerprint(script_path) if script_path else None,
        "note": "指纹不含时间戳：同一输入与参数必须产生逐位相同的 manifest（可复现纪律）。"
                "语料或词表变了 → 这里必须变；没变而数字变了 → 说明方法/脚本变了（配合 --prev 排查）。",
    }
    if reference:
        man["reference"] = file_fingerprint(reference)
    if extra:
        man["extra"] = extra
    return man


def compare(a, b):
    """比较两份 manifest，返回差异列表（用于 --prev 的漂移报告）。按**角色**对齐输入。"""
    out = []
    if not a or not b:
        return out
    ai = {(x.get("role") or x.get("name")): x for x in (a.get("inputs") or [])}
    bi = {(x.get("role") or x.get("name")): x for x in (b.get("inputs") or [])}
    for role in sorted(set(ai) | set(bi)):
        x, y = ai.get(role), bi.get(role)
        if x is None:
            out.append({"item": "输入新增", "detail": "%s：%s" % (role, y.get("name"))})
        elif y is None:
            out.append({"item": "输入消失", "detail": "%s：%s" % (role, x.get("name"))})
        elif x.get("sha256") != y.get("sha256"):
            out.append({"item": "输入内容变化", "detail": "%s：%s → %s"
                        % (role, (x.get("sha256") or "")[:12], (y.get("sha256") or "")[:12])})
        elif x.get("name") != y.get("name"):
            out.append({"item": "同角色换了文件（内容相同）", "detail": "%s：%s → %s"
                        % (role, x.get("name"), y.get("name"))})
    if a.get("vocabulary_sha256") != b.get("vocabulary_sha256"):
        out.append({"item": "词表变化", "detail": "%s → %s"
                    % ((a.get("vocabulary_sha256") or "无")[:12], (b.get("vocabulary_sha256") or "无")[:12])})
    sa, sb = (a.get("script") or {}), (b.get("script") or {})
    if sa.get("sha256") != sb.get("sha256"):
        out.append({"item": "脚本变化", "detail": "%s → %s"
                    % ((sa.get("sha256") or "无")[:12], (sb.get("sha256") or "无")[:12])})
    aa, ab = (a.get("args") or {}), (b.get("args") or {})
    for k in sorted(set(aa) | set(ab)):
        if aa.get(k) != ab.get(k):
            out.append({"item": "参数变化", "detail": "%s：%r → %r" % (k, aa.get(k), ab.get(k))})
    return out
