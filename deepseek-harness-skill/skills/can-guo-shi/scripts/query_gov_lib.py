#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""query_gov_lib.py — 国务院政策文件库 JSON 接口查询（零依赖，仅标准库）。

用法（任选其一）：
    python query_gov_lib.py params.json [out.json]
    python query_gov_lib.py --q "营商环境" [--searchfield title] [--n 10] [--out out.json]
    python query_gov_lib.py "营商环境"          # 裸词 = --q，适合快速探测

params.json / 旗标支持的键（除 q 外均可省略）：
    q            必填：查询关键词
    searchfield  title(默认) | content
    sort         score(默认) | pubtime
    p            页码，从 0 起
    n            每页条数（默认 10）
    pubtimeyear  年份过滤，如 "2025"
    bmfl         发文机关，如 "国家发展和改革委员会"
    childtype    gongwen | bumenfile | otherfile
    mintime      开始日期 YYYY-MM-DD
    maxtime      结束日期 YYYY-MM-DD

输出（out.json，UTF-8）：
    成功: {"ok": true, "query": {...}, "total": N,
          "items": [{"title","doc_no","agency","pubtime_iso","childtype","url"}],
          "fetched_at": "..."}
    失败: {"ok": false, "error": "...", "fallback": "use WebSearch"}，退出码 1。

设计约束（继承参国是「扩展工具」设计原则 + 本机 DSH/Windows 契约）：
    - 零依赖：仅 urllib/json/re/sys/datetime
    - 诚实降级：网络/解析失败输出原因并提示回退 WebSearch，不静默假成功
    - 参数经 JSON 文件落盘传递（避免 PowerShell 引号吞掉参数）
    - 契约细节见 ../references/policy-sources.md（接口参数/响应/字段映射/陷阱）
"""

import datetime as _dt
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://sousuo.www.gov.cn/search-gov/data"
TOPIC = "zhengcelibrary"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) dsh-can-guo-shi/0.1"
TIMEOUT = 20
EM_TAG = re.compile(r"</?em>")
DEFAULTS = {"searchfield": "title", "sort": "score", "p": 0, "n": 10}
STR_KEYS = ("q", "searchfield", "sort", "pubtimeyear", "bmfl", "childtype", "mintime", "maxtime")
INT_KEYS = ("p", "n")


def parse_args(argv):
    """argv → (params, out_path, err)。支持 --key value 旗标与参数 JSON 文件混用。
    位置参数约定：第一个存在的 .json 为参数文件；第二个 .json 为输出文件；
    无 .json 时第一个裸词视为查询关键词。"""
    params = dict(DEFAULTS)
    positional = []
    out = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            key = a[2:]
            if key == "out":
                if i + 1 < len(argv):
                    out = argv[i + 1]
                    i += 2
                else:
                    i += 1
                continue
            if i + 1 < len(argv):
                params[key] = argv[i + 1]
                i += 2
            else:
                i += 1
        else:
            positional.append(a)
            i += 1
    json_pos = [p for p in positional if p.lower().endswith(".json")]
    if json_pos:
        try:
            with open(json_pos[0], encoding="utf-8") as f:
                params.update(json.load(f))
        except Exception as e:  # noqa: BLE001 — 参数文件损坏也须诚实降级
            return None, out, f"参数文件读取失败: {e}"
        if out is None and len(json_pos) > 1:
            out = json_pos[1]
    elif "q" not in params and positional:
        params["q"] = positional[0]
    if not str(params.get("q", "")).strip():
        return None, out, "缺少必填参数 q（查询关键词）"
    return params, out, None


def fetch(params):
    """发起 GET 请求并返回解析后的 JSON。网络异常向上抛出由 main 降级处理。"""
    qs = {"t": TOPIC}
    for k in STR_KEYS:
        if k in params and params[k] not in (None, ""):
            qs[k] = str(params[k])
    for k in INT_KEYS:
        try:
            qs[k] = str(int(params.get(k, DEFAULTS[k])))
        except (TypeError, ValueError):
            qs[k] = str(DEFAULTS[k])
    url = API + "?" + urllib.parse.urlencode(qs)
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _clean(text):
    """剥离 <em> 高亮与 <br> 换行，压缩空白。实测（2026-09-04）gongbao 类标题含 <br>。"""
    t = EM_TAG.sub("", text or "")
    t = re.sub(r"<br\s*/?>", " ", t, flags=re.IGNORECASE)
    t = t.replace("\u3000", " ").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", t).strip()


def _iso(ms):
    """毫秒时间戳 → 'YYYY-MM-DD'（本地时区）。"""
    try:
        return _dt.datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_dot_date(s):
    """'2022.10.31' 类日期串 → 'YYYY-MM-DD'（pubtimeStr 兜底）。"""
    m = re.match(r"\s*(\d{4})[./\-](\d{1,2})[./\-](\d{1,2})", s or "")
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


def normalize(data):
    """searchVO.catMap → (total, items)。

    实测结构（2026-09-04）：catMap 含 gongwen/bumenfile/otherfile/gongbao 四类，
    顶层 totalCount 恒为 0，须取各分类 totalCount 之和；
    发布机构字段为 puborg（source 为空串）；原文链接为 url（piclinksurl 为空串）。
    """
    items = []
    search_vo = data.get("searchVO") or {}
    cat_map = search_vo.get("catMap") or {}
    for cat_key, cat in cat_map.items():
        for it in cat.get("listVO") or []:
            items.append({
                "title": _clean(it.get("title")),
                "doc_no": (it.get("pcode") or "").strip() or None,
                "agency": (it.get("puborg") or it.get("source") or "").strip() or None,
                "pubtime_iso": _iso(it.get("pubtime")) or _parse_dot_date(it.get("pubtimeStr")),
                "childtype": cat_key,
                "url": it.get("url") or it.get("piclinksurl"),
            })
    top = search_vo.get("totalCount")
    if isinstance(top, int) and top > 0:
        total = top
    else:
        total = sum(int(c.get("totalCount") or 0) for c in cat_map.values()) or None
    return total, items


def _write(path, payload):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _fail(out, message):
    payload = {"ok": False, "error": message, "fallback": "use WebSearch"}
    if out:
        _write(out, payload)
    print(message, file=sys.stderr)
    return 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    params, out, err = parse_args(argv)
    if err:
        # 参数错误（如缺 q）只报错不落盘，避免在不明输出路径时写默认文件
        return _fail(out, err)
    out = out or "gov_lib_out.json"
    try:
        data = fetch(params)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        return _fail(out, f"接口请求失败（{type(e).__name__}）: {e}")
    except ValueError as e:
        return _fail(out, f"接口响应非 JSON: {e}")
    try:
        total, items = normalize(data)
    except Exception as e:  # noqa: BLE001 — 官网改版导致的结构变化
        return _fail(out, f"响应解析失败（官网结构可能改版）: {e}")
    payload = {
        "ok": True,
        "query": {k: params.get(k) for k in (*STR_KEYS, *INT_KEYS) if k in params},
        "total": total,
        "items": items,
        "fetched_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _write(out, payload)
    print(f"ok: total={total}, items={len(items)} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
