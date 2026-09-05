#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""query_flk.py — 国家法律法规数据库（flk.npc.gov.cn）检索脚本（零依赖，仅标准库）。

用法（任选其一）：
    python query_flk.py params.json [out.json]
    python query_flk.py --q "行政处罚法" [--searchRange 1] [--searchType 2] [--sxx 3] [--out out.json]
    python query_flk.py --q "行政处罚法" --sxx 3 --out 现行有效.json      # 只看现行有效
    python query_flk.py --detail <bbbs> --out detail.json               # 单条详情

参数（params.json / 旗标）：
    q            必填：检索词
    searchRange  1=标题（默认） | 2=正文
    searchType   1=精确 | 2=模糊（默认）
    sxx          时效过滤：3=现行有效（实测确定）；不传=全部
    pageNum      页码（默认 1）
    pageSize     每页条数（默认 10，接口支持至 ~100）
    sort         默认相关度；可传 gbrq(公布日期)/sxrq(施行日期)/sxx/flxz/zdjg

输出（out.json，UTF-8）：
    成功: {"ok": true, "query": {...}, "total": N,
          "items": [{"title","kind","agency","pub_date","eff_date",
                     "status_code","status_label","bbbs","url"}], "fetched_at": ...}
    失败: {"ok": false, "error": "...", "fallback": "use WebSearch"}，退出码 1。

status_label 映射（2026-09-04 实测样本推定，详见 ../references/policy-sources.md）：
    3=现行有效（确定）；2=已失效或已被修改（推定）；1=已废止（推定）；
    缺失/其他码 status_code 保留原值、status_label 为 None。精确语义以 flk 官网时效筛选项为准。

--detail <bbbs>：GET flfgDetails 拉详情（含 ossFile 全文路径、xgwj 关联文件计数）。

设计约束：零依赖、参数 JSON 落盘、失败诚实降级（fallback: use WebSearch）、需 UA+Referer。
"""

import datetime as _dt
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://flk.npc.gov.cn"
LIST_URL = BASE + "/law-search/search/list"
DETAIL_URL = BASE + "/law-search/search/flfgDetails"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 dsh-can-guo-shi/0.1"
TIMEOUT = 25
DEFAULTS = {"searchRange": 1, "searchType": 2, "pageNum": 1, "pageSize": 10}
STR_KEYS = ("q", "searchRange", "searchType", "sxx", "pageNum", "pageSize", "sort")

STATUS_LABEL = {1: "已废止（推定）", 2: "已失效或已被修改（推定）", 3: "现行有效"}


def parse_args(argv):
    """argv → (params, out_path, mode_detail, err)。用法与 query_gov_lib.parse_args 一致。"""
    params = dict(DEFAULTS)
    positional = []
    out = None
    detail = False
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
            if key == "detail":
                detail = True
                if i + 1 < len(argv):
                    params["bbbs"] = argv[i + 1]
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
        except Exception as e:  # noqa: BLE001
            return None, out, detail, f"参数文件读取失败: {e}"
        if out is None and len(json_pos) > 1:
            out = json_pos[1]
    elif "q" not in params and not detail and positional:
        params["q"] = positional[0]
    if not detail and not str(params.get("q", "")).strip():
        return None, out, detail, "缺少必填参数 q（检索词）"
    if detail and not str(params.get("bbbs", "")).strip():
        return None, out, detail, "缺少 --detail 所需的 bbbs 参数"
    return params, out, detail, None


def _request(url, payload=None, referer=None):
    """GET（payload=None）或 POST JSON。返回解析后的 dict。"""
    headers = {"User-Agent": UA, "Accept": "application/json"}
    ref = referer or (BASE + "/search" if payload is not None else BASE + "/detail")
    headers["Referer"] = ref
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    else:
        req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def fetch_list(params):
    """构造并发送检索请求。sxx 参数转为数组（如 '3' → [3]）。"""
    payload = {
        "searchRange": int(params.get("searchRange", 1)),
        "sxrq": [], "gbrq": [], "sxx": [],
        "searchType": int(params.get("searchType", 2)),
        "xgzlSearch": False,
        "searchContent": str(params.get("q", "")).strip(),
        "orderByParam": {"order": "-1", "sort": str(params.get("sort", ""))},
        "flfgCodeId": [], "zdjgCodeId": [], "gbrqYear": [],
        "pageNum": int(params.get("pageNum", 1)),
        "pageSize": int(params.get("pageSize", 10)),
    }
    sxx = str(params.get("sxx", "")).strip()
    if sxx:
        payload["sxx"] = [int(sxx)]
    return _request(LIST_URL, payload, referer=BASE + "/search")


def fetch_detail(bbbs):
    return _request(DETAIL_URL + "?bbbs=" + urllib.parse.quote(bbbs), referer=BASE + "/detail?id=" + urllib.parse.quote(bbbs))


def _clean(text):
    """剥离 <em class='highlight'> 等标签并压缩空白。"""
    t = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", t).strip()


def _label(code):
    if code is None:
        return None
    return STATUS_LABEL.get(code, str(code))


def normalize_list(data):
    items = []
    for r in data.get("rows") or []:
        code = r.get("sxx")
        items.append({
            "title": _clean(r.get("title")),
            "kind": (r.get("flxz") or "").strip() or None,
            "agency": (r.get("zdjgName") or "").strip() or None,
            "pub_date": r.get("gbrq") or None,
            "eff_date": r.get("sxrq") or None,
            "status_code": code,
            "status_label": _label(code),
            "bbbs": r.get("bbbs"),
            "url": BASE + "/detail?id=" + urllib.parse.quote(str(r.get("bbbs") or "")),
        })
    return data.get("total"), items


def normalize_detail(data):
    d = data.get("data") or {}
    return {
        "title": (d.get("title") or "").strip() or None,
        "kind": d.get("flxz"),
        "agency": d.get("zdjgName"),
        "pub_date": d.get("gbrq"),
        "eff_date": d.get("sxrq"),
        "status_code": d.get("sxx"),
        "status_label": _label(d.get("sxx")),
        "bbbs": d.get("bbbs"),
        "xgwj_count": len(d.get("xgwj")) if isinstance(d.get("xgwj"), list) else None,
        "oss": d.get("ossFile"),
        "url": BASE + "/detail?id=" + urllib.parse.quote(str(d.get("bbbs") or "")),
    }


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
    params, out, detail, err = parse_args(argv)
    if err:
        return _fail(out, err)
    out = out or ("flk_detail_out.json" if detail else "flk_out.json")
    try:
        if detail:
            data = fetch_detail(str(params["bbbs"]))
            total, items = None, normalize_detail(data)
        else:
            data = fetch_list(params)
            total, items = normalize_list(data)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        return _fail(out, f"接口请求失败（{type(e).__name__}）: {e}")
    except ValueError as e:
        return _fail(out, f"接口响应非 JSON: {e}")
    except Exception as e:  # noqa: BLE001 — 官网改版导致的结构变化
        return _fail(out, f"响应解析失败（官网结构可能改版）: {e}")
    payload = {
        "ok": True,
        "query": {k: params.get(k) for k in STR_KEYS if k in params},
        "total": total,
        "items": items,
        "fetched_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _write(out, payload)
    print(f"ok: total={total} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
