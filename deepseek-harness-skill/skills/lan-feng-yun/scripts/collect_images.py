"""
揽风云 辅助脚本 — 图片采集流水线
从新闻文章 URL 列表中自动提取、下载、编码图片，一步返回可嵌入的 Data URI。

用法：
    python collect_images.py '<JSON输入>' [--max-images 5] [--max-kb 200] [--output result.json]

JSON 输入格式：
    [{"url": "https://...", "label": "来源名称"}, ...]

输出（STDOUT）：
    轻量摘要 JSON: {"ok": true, "total": 21, "selected": 3, "output_file": "result.json"}
    若未指定 --output，则直接输出完整结果（小图场景可用）。

输出（文件，指定 --output 时）：
    完整 JSON 数组，含 data_uri 等字段。

依赖：pip install requests beautifulsoup4 Pillow（Pillow 可选）
"""

import sys, json, re, base64, io, os
from html.parser import HTMLParser

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

try:
    from PIL import Image
    _HAS_PILLOW = True
except ImportError:
    _HAS_PILLOW = False

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"

# ── 正文容器标记（用于判断图片是否在文章正文内）──────────────
_BODY_TAGS = {"article", "main", "section"}
_BODY_CLASS_KEYWORDS = ["article", "content", "post", "body", "text", "detail", "story", "entry"]


def _is_in_body(parent_chain: str) -> bool:
    """根据父元素链判断图片是否在正文区域。"""
    chain_lower = parent_chain.lower()
    for kw in _BODY_CLASS_KEYWORDS:
        if kw in chain_lower:
            return True
    return False


def _extract_alt(attrs: dict) -> str:
    """从属性字典中提取 alt 文本。"""
    alt = attrs.get("alt", "") or attrs.get("title", "")
    return alt.strip()


def _extract_parent_text(html: str, pos: int, window: int = 200) -> str:
    """从 img 标签位置向前后取窗口字符作为上下文文本。"""
    start = max(0, pos - window)
    end = min(len(html), pos + window)
    snippet = html[start:end]
    # 去除 HTML 标签，保留纯文本
    text = re.sub(r"<[^>]+>", " ", snippet)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:300]


def _get_parent_tag(html: str, pos: int) -> str:
    """从 img 标签位置向前找最近的父元素标签名和 class。"""
    before = html[max(0, pos - 500) : pos]
    tags = re.findall(r"<(\w+)[^>]*class\s*=\s*[\"']([^\"']*)[\"'][^>]*>", before, re.I)
    if tags:
        tag, cls = tags[-1]
        return f"{tag}.{cls}"
    tags2 = re.findall(r"<(\w+)[^>]*>", before, re.I)
    if tags2:
        return tags2[-1]
    return "unknown"


# ── 图片提取（含上下文）────────────────────────────────────────

def extract_images(html: str, article_url: str) -> list:
    """从 HTML 提取图片 URL + 上下文信息。返回 dict 列表。"""
    results = []
    seen = set()

    # 联合模式：src / data-src / data-original
    pattern = r"""<\s*img[^>]+(?:src|data-src|data-original)\s*=\s*["']([^"']+\.(?:jpg|jpeg|png|webp)[^"']*)["']"""
    for m in re.finditer(pattern, html, re.I):
        u = m.group(1)
        if u.startswith("//"):
            u = "https:" + u
        elif u.startswith("/"):
            u = "/".join(article_url.split("/")[:3]) + u

        u_lower = u.lower()
        if len(u) < 40:
            continue
        if any(bad in u_lower for bad in ["icon", "logo", "avatar", "1x1", "pixel",
                "qr_code", "favicon", "emoji", "banner", "thumb_default", "push_qrcode",
                "chrome.jpg", "ad_", "/ad/"]):
            continue
        if u in seen:
            continue
        seen.add(u)

        pos = m.start()
        # 提取完整 img 标签的属性
        tag_text = html[pos : pos + 600]  # img 标签 + 附近
        alt_match = re.search(r"""alt\s*=\s*["']([^"']*)["']""", tag_text, re.I)
        alt = alt_match.group(1).strip() if alt_match else ""

        parent_tag = _get_parent_tag(html, pos)
        surrounding = _extract_parent_text(html, pos)
        is_in_body = _is_in_body(parent_tag)

        results.append({
            "url": u,
            "alt": alt,
            "surrounding_text": surrounding,
            "parent_tag": parent_tag,
            "is_in_body": is_in_body,
        })

    return results


# ── 下载 / 编码 ────────────────────────────────────────────────

def try_download(img_url, referers, timeout=12):
    if not _HAS_REQUESTS:
        raise RuntimeError("requests 未安装")
    last_err = None
    for ref in referers:
        try:
            resp = _requests.get(img_url, headers={
                "User-Agent": UA,
                "Referer": ref,
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            }, timeout=timeout)
            resp.raise_for_status()
            return resp.content, ref
        except Exception as e:
            last_err = e
    raise last_err or RuntimeError("下载失败")


def encode_image(data, max_kb=200):
    if _HAS_PILLOW:
        try:
            img = Image.open(io.BytesIO(data))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            w, h = img.size
            if w > 500:
                ratio = 500 / w
                img = img.resize((500, int(h * ratio)), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70)
            encoded = base64.b64encode(buf.getvalue()).decode("ascii")
            return {"data_uri": f"data:image/jpeg;base64,{encoded}",
                    "size_kb": round(len(buf.getvalue()) / 1024, 1), "format": "jpeg", "width": img.size[0]}
        except Exception:
            pass

    size_kb = len(data) / 1024
    if size_kb > max_kb:
        return None
    mime = "image/jpeg"
    if data[:4] == b"\x89PNG":
        mime = "image/png"
    elif data[:3] == b"GIF":
        mime = "image/gif"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime = "image/webp"
    encoded = base64.b64encode(data).decode("ascii")
    return {"data_uri": f"data:{mime};base64,{encoded}", "size_kb": round(size_kb, 1), "format": mime.split("/")[-1]}


# ── 打分（v2：利用上下文信息）───────────────────────────────────

def score_candidate(img_info: dict) -> int:
    """基于 URL、alt、DOM 位置综合打分。"""
    s = 0
    url_lower = img_info["url"].lower()

    # 正向信号
    if img_info["is_in_body"]:
        s += 8  # 在正文区域内 → 大幅加分
    alt = img_info.get("alt", "")
    if alt and len(alt) > 4:
        s += 3  # 有有意义的 alt 文本
    if any(k in url_lower for k in ["1080", "1920", "large", "original", "w700", "w1080"]):
        s += 1
    if re.search(r"202[56]\d{2}", url_lower):
        s += 1

    # 负向信号
    if not img_info["is_in_body"]:
        s -= 6  # 不在正文内 → 扣分（可能是推荐流/侧栏/广告）
    if alt and any(bad in alt for bad in ["广告", "推荐", "海报", "萌宠", "宠物", "狮子", "斑马",
            "跑男", "综艺", "游戏", "下载", "APP", "二维码", "扫码"]):
        s -= 10  # alt 中含明显无关关键词 → 严厉扣分
    parent = img_info.get("parent_tag", "")
    if any(bad in parent.lower() for bad in ["aside", "sidebar", "recommend", "ad", "footer", "nav"]):
        s -= 5  # 非正文 DOM 区域
    if re.search(r"[a-f0-9]{32,}", url_lower):
        s -= 1

    return s


# ── 主逻辑 ─────────────────────────────────────────────────────

def collect(articles, max_images=5, max_kb=200, event_keywords=None):
    """主逻辑：遍历文章 → 提取图片+上下文 → 下载 → 编码 → 评分排序返回。"""
    results = []
    seen_urls = set()
    if event_keywords is None:
        event_keywords = []

    for art in articles:
        url = art["url"]
        label = art.get("label", url[:60])
        referers = art.get("referers", [])
        if not referers:
            domain = "/".join(url.split("/")[:3])
            referers = ["", domain]

        # 1. 抓取 HTML
        try:
            html = fetch_html(url)
        except Exception as e:
            print(f"[SKIP] 页面抓取失败: {label} — {e}", file=sys.stderr)
            continue
        if not html:
            continue

        # 2. 提取图片 + 上下文
        img_infos = extract_images(html, url)
        print(f"[EXTRACT] {label}: {len(img_infos)} 候选图片", file=sys.stderr)

        # 打印上下文摘要（调试用）
        for ii in img_infos:
            inbody = "BODY" if ii["is_in_body"] else "SIDE"
            alt_preview = ii["alt"][:40] if ii["alt"] else "(无alt)"
            print(f"  [{inbody}] {alt_preview} — {ii['url'][:80]}", file=sys.stderr)

        # 3. 下载 + 编码
        for ii in img_infos:
            img_url = ii["url"]
            if img_url in seen_urls:
                continue
            seen_urls.add(img_url)

            try:
                raw, used_ref = try_download(img_url, referers)
            except Exception as e:
                print(f"[FAIL] 下载: {img_url[:100]}... — {type(e).__name__}", file=sys.stderr)
                continue

            encoded = encode_image(raw, max_kb)
            if encoded is None:
                print(f"[SKIP] 超限: {img_url[:100]}... — {len(raw)/1024:.0f}KB", file=sys.stderr)
                continue

            score = score_candidate(ii)
            encoded.update({
                "source_url": img_url,
                "source_label": label,
                "alt": ii["alt"],
                "surrounding_text": ii["surrounding_text"],
                "parent_tag": ii["parent_tag"],
                "is_in_body": ii["is_in_body"],
                "score": score,
            })
            results.append(encoded)
            print(f"[OK] score={score} {encoded['size_kb']}KB [{('BODY' if ii['is_in_body'] else 'SIDE')}] alt='{ii['alt'][:30]}'", file=sys.stderr)

    # 4. 按分数排序去重
    results.sort(key=lambda r: r["score"], reverse=True)
    deduped = []
    for r in results:
        is_dup = False
        for d in deduped:
            if d["source_label"] == r["source_label"] and abs(d["size_kb"] - r["size_kb"]) < 5:
                is_dup = True
                break
        if not is_dup:
            deduped.append(r)

    return deduped[:max_images]


def fetch_html(url, timeout=15):
    if not _HAS_REQUESTS:
        return None
    resp = _requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


# ── CLI ─────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("用法: python collect_images.py '<JSON>' [--max-images 5] [--max-kb 200] [--output path.json] [--keywords 关键词1,关键词2]", file=sys.stderr)
        sys.exit(1)

    articles = json.loads(sys.argv[1])
    max_images = 5
    max_kb = 200
    output_path = None
    keywords = []

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--max-images" and i + 1 < len(sys.argv):
            max_images = int(sys.argv[i + 1]); i += 2
        elif sys.argv[i] == "--max-kb" and i + 1 < len(sys.argv):
            max_kb = int(sys.argv[i + 1]); i += 2
        elif sys.argv[i] == "--output" and i + 1 < len(sys.argv):
            output_path = sys.argv[i + 1]; i += 2
        elif sys.argv[i] == "--keywords" and i + 1 < len(sys.argv):
            keywords = [k.strip() for k in sys.argv[i + 1].split(",") if k.strip()]; i += 2
        else:
            i += 1

    results = collect(articles, max_images, max_kb, event_keywords=keywords)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, ensure_ascii=False, fp=f)
        summary = {
            "ok": True,
            "total_candidates": len(results),
            "output_file": output_path,
            "top_scores": [{"score": r["score"], "size_kb": r["size_kb"],
                            "alt": r.get("alt", "")[:50], "is_in_body": r.get("is_in_body"),
                            "source_label": r["source_label"]}
                           for r in results[:max_images]],
        }
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
