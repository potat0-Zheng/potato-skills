# -*- coding: utf-8 -*-
"""
揽风云 辅助脚本 — 轻量级网页正文抓取（纯标准库版，无 bs4 依赖）

用法：
    python fetch_page.py <URL> [--timeout 15]
输出：
    UTF-8 纯文本：标题 + 正文（去除 HTML 标签）

依赖：
    无强制第三方依赖。requests 可用时优先使用（自动处理编码），
    否则回退到 urllib（标准库）；HTML 解析使用标准库 html.parser。

编码提示（DSH/Windows）：
    中文输出建议由调用方写入 UTF-8 文件，或先设
    $env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
"""

import sys
import re
from html.parser import HTMLParser
from urllib.request import Request, urlopen

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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
}

_SKIP_TAGS = {"script", "style", "nav", "footer", "header", "aside", "noscript"}
_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "br", "div", "td", "blockquote", "section"}


class TextExtractor(HTMLParser):
    """提取 <title> 与正文块级文本，跳过脚本/导航/页脚等噪音区。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.blocks = []
        self._in_title = False
        self._skip_depth = 0
        self._cur = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = True
        elif tag in _BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip_depth:
            return
        text = data.strip()
        if text:
            self._cur.append(text)

    def _flush(self):
        if self._cur:
            self.blocks.append(" ".join(self._cur))
            self._cur = []


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def fetch(url: str, timeout: int = 15):
    """返回 (title, body)。requests 优先，urllib 回退。"""
    if _HAS_REQUESTS:
        resp = _requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        html = resp.text
    else:
        req = Request(url, headers=HEADERS)
        with urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            enc = resp.headers.get_content_charset() or "utf-8"
            html = data.decode(enc, errors="replace")

    ex = TextExtractor()
    ex.feed(html)
    title = clean(ex.title) or "(no title)"

    # 去相邻重复行，过滤过短噪音片段
    seen = set()
    lines = []
    for b in ex.blocks:
        line = clean(b)
        if not line or len(line) < 2:
            continue
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
    body = "\n".join(lines)

    max_chars = 15000
    if len(body) > max_chars:
        body = body[:max_chars] + "\n\n[... 正文过长，已截断至前 {} 字符]".format(max_chars)
    return title, body


def main():
    if len(sys.argv) < 2:
        print("用法: python fetch_page.py <URL> [--timeout 15]", file=sys.stderr)
        sys.exit(1)

    timeout = 15
    urls = []
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--timeout" and i + 1 < len(sys.argv):
            timeout = int(sys.argv[i + 1]); i += 2
        else:
            urls.append(sys.argv[i]); i += 1

    for url in urls:
        try:
            title, body = fetch(url, timeout)
        except Exception as e:
            print("[错误] {}: {}: {}".format(url, type(e).__name__, e), file=sys.stderr)
            continue
        # SPA/空壳检测：正文极短时提示可能为 JS 渲染、登录墙或反爬
        body_len = len(body.strip())
        if body_len < 50:
            print(
                "[提示] {}: 提取到正文过短（{} 字符）。页面可能为 JS 渲染（SPA）、需要登录，"
                "或触发反爬。建议改用 WebSearch snippet 层或平台 API"
                "（见 references/source-fallback.md 平台能力清单）。".format(url, body_len),
                file=sys.stderr,
            )
        print(title)
        print("=" * len(title))
        print(body)
        print()


if __name__ == "__main__":
    main()
