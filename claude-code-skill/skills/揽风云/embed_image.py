"""
揽风云 辅助脚本 — 图片下载与 base64 嵌入
将外部图片 URL 下载、压缩、编码为 Data URI，供 HTML 内联嵌入。

用法：
    python embed_image.py <URL> [--max-width 400] [--quality 70] [--max-raw-kb 150]

输出（成功时）：
    JSON: {"ok": true, "data_uri": "data:image/jpeg;base64,...", "size_kb": 34, "width": 400, "format": "jpeg"}

输出（回退时——无 Pillow 且文件过大，或被墙）：
    JSON: {"ok": false, "reason": "...", "fallback_url": "<原URL>"}

依赖（可选升级）：
    pip install Pillow   # 有则自动缩放压缩，无则回退到裸字节上限
    pip install requests # 有则优先用 requests，无则回退到 urllib（标准库）

策略：
    ├── Pillow 可用 → 下载 → 缩放到 max-width → JPEG quality% → base64
    ├── Pillow 不可用 → 下载 → 原始 ≤ max_raw_kb → base64
    └── 超出上限 / 下载失败 → 返回 fallback URL
"""

import sys
import json
import base64
import io
import os

# ── HTTP 下载：优先 requests，回退 urllib ──────────────────────
try:
    import requests as _requests

    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


def download(url: str, timeout: int = 15) -> bytes:
    """下载图片原始字节。优先 requests，回退 urllib。"""
    if _HAS_REQUESTS:
        resp = _requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp.content
    else:
        from urllib.request import Request, urlopen

        req = Request(url, headers=HEADERS)
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()


# ── 图像处理：优先 Pillow，回退裸字节 ─────────────────────────
try:
    from PIL import Image

    _HAS_PILLOW = True
except ImportError:
    _HAS_PILLOW = False


def process_with_pillow(data: bytes, max_width: int, quality: int) -> dict:
    """Pillow 路径：缩放 + JPEG 压缩。"""
    img = Image.open(io.BytesIO(data))

    # RGBA → RGB（JPEG 不支持透明通道）
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    # 缩放
    w, h = img.size
    if w > max_width:
        ratio = max_width / w
        img = img.resize((max_width, int(h * ratio)), Image.LANCZOS)  # type: ignore[attr-defined]
        w, h = img.size

    # 编码
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")

    return {
        "ok": True,
        "data_uri": f"data:image/jpeg;base64,{encoded}",
        "size_kb": round(len(buf.getvalue()) / 1024, 1),
        "width": w,
        "height": h,
        "format": "jpeg",
    }


def process_raw(data: bytes, max_kb: int, url: str) -> dict:
    """无 Pillow 回退：仅检查大小上限。"""
    size_kb = len(data) / 1024
    if size_kb > max_kb:
        return {
            "ok": False,
            "reason": f"图片 {size_kb:.0f}KB 超出裸字节上限 {max_kb}KB（无 Pillow 无法压缩），保留链接回退",
            "fallback_url": url,
            "size_kb": round(size_kb, 1),
        }

    # 尝试推断 MIME
    mime = "image/jpeg"
    if data[:4] == b"\x89PNG":
        mime = "image/png"
    elif data[:3] == b"GIF":
        mime = "image/gif"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime = "image/webp"

    encoded = base64.b64encode(data).decode("ascii")
    return {
        "ok": True,
        "data_uri": f"data:{mime};base64,{encoded}",
        "size_kb": round(size_kb, 1),
        "format": mime.split("/")[-1],
    }


# ── 主入口 ─────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "reason": "用法: python embed_image.py <URL> [--max-width 400] [--quality 70] [--max-raw-kb 150]"}, ensure_ascii=False))
        sys.exit(1)

    url = sys.argv[1]
    max_width = 400
    quality = 70
    max_raw_kb = 150

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--max-width" and i + 1 < len(sys.argv):
            max_width = int(sys.argv[i + 1]); i += 2
        elif sys.argv[i] == "--quality" and i + 1 < len(sys.argv):
            quality = int(sys.argv[i + 1]); i += 2
        elif sys.argv[i] == "--max-raw-kb" and i + 1 < len(sys.argv):
            max_raw_kb = int(sys.argv[i + 1]); i += 2
        else:
            i += 1

    try:
        data = download(url)
    except Exception as e:
        print(json.dumps({"ok": False, "reason": f"下载失败: {e}", "fallback_url": url}, ensure_ascii=False))
        sys.exit(2)

    if _HAS_PILLOW:
        result = process_with_pillow(data, max_width, quality)
    else:
        result = process_raw(data, max_raw_kb, url)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
