# -*- coding: utf-8 -*-
"""bai-bao-dai 依赖检查与初始化提示。

用法：python setup.py
- 检查 Python 版本、核心依赖（requests）
- 检查知乎内嵌爬虫与 cookie 是否就绪
- 检查外部爬虫（MediaCrawler/weiboSpider/Spider_XHS）部署与凭证状态
"""
import importlib
import json
import os
import sys

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
EXTERNAL_DIR = os.path.join(SKILL_DIR, "external")
OK, MISS = "[OK]", "[MISS]"


def _has_zhihu_cookie():
    p = os.path.join(SKILL_DIR, "zhihu", "cookie.txt")
    if not os.path.exists(p):
        return False
    with open(p, "r", encoding="utf-8") as f:
        return bool(f.read().strip())


def _mc_session(platform_dir):
    """MediaCrawler 持久化浏览器登录态是否可用。"""
    d = os.path.join(EXTERNAL_DIR, "MediaCrawler", "browser_data", platform_dir)
    return os.path.isdir(d) and bool(os.listdir(d))


def _mc_env_cookie(key):
    """MediaCrawler .env 中是否配置了指定平台 cookie。"""
    p = os.path.join(EXTERNAL_DIR, "MediaCrawler", ".env")
    if not os.path.exists(p):
        return False
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(key) and "=" in line and line.split("=", 1)[1].strip():
                    return True
    except Exception:
        pass
    return False


def _spider_xhs_cookie():
    """Spider_XHS cookie 文件是否已配置（cookies.txt）。"""
    p = os.path.join(EXTERNAL_DIR, "Spider_XHS", "cookies.txt")
    if not os.path.exists(p):
        return False
    try:
        with open(p, "r", encoding="utf-8") as f:
            return bool(f.read().strip())
    except Exception:
        return False


def check():
    ok = True
    print("=" * 50)
    print("bai-bao-dai 依赖检查")
    print("=" * 50)

    # Python 版本
    py = sys.version_info
    print(f"[Python] {py.major}.{py.minor}.{py.micro}")
    if py < (3, 10):
        print("  [!] 建议 Python 3.10+（小红书 Spider_XHS 要求）")
        ok = False

    # 核心依赖
    for mod in ("requests",):
        try:
            importlib.import_module(mod)
            print(f"[依赖] {mod} {OK}")
        except ImportError:
            print(f"[依赖] {mod} {MISS} 需安装：pip install {mod}")
            ok = False

    # 可选依赖
    for mod, hint in (("playwright", "MediaCrawler 需：pip install playwright && playwright install"),
                      ("bs4", "可选：pip install beautifulsoup4")):
        try:
            importlib.import_module(mod)
            print(f"[可选] {mod} {OK}")
        except ImportError:
            print(f"[可选] {mod} -（{hint}）")

    # 知乎内嵌爬虫
    crawler = os.path.join(SKILL_DIR, "zhihu", "crawl.py")
    print(f"[知乎] crawl.py {OK if os.path.exists(crawler) else MISS}")
    print(f"[知乎] cookie.txt {OK if _has_zhihu_cookie() else MISS}")

    # 外部爬虫：部署 + 凭证双状态
    print("-" * 50)
    print("外部爬虫（external/）：")
    mc = os.path.join(EXTERNAL_DIR, "MediaCrawler")
    if os.path.isdir(mc):
        wb = _mc_session("wb_user_data_dir") or _mc_env_cookie("WB_COOKIE")
        xhs = _mc_session("xhs_user_data_dir") or _mc_env_cookie("XHS_COOKIE")
        print(f"[MediaCrawler] 已部署 | 微博凭证 {OK if wb else MISS} | 小红书凭证 {OK if xhs else MISS}")
    else:
        print(f"[MediaCrawler] {MISS} 未部署（微博/小红书关键词搜索主爬虫）")
        ok = False
    if os.path.isdir(os.path.join(EXTERNAL_DIR, "weiboSpider")):
        cfg = os.path.join(EXTERNAL_DIR, "weiboSpider", "weibo_spider", "config.json")
        print(f"[weiboSpider] 已部署 | config.json {OK if os.path.exists(cfg) else MISS}（按用户爬取，需 cookie+user_id）")
    else:
        print("[weiboSpider] 未部署（微博按用户爬取，可选）")
    if os.path.isdir(os.path.join(EXTERNAL_DIR, "Spider_XHS")):
        print(f"[Spider_XHS] 已部署 | cookies.txt {OK if _spider_xhs_cookie() else MISS}（或每次扫码登录）")
    else:
        print("[Spider_XHS] 未部署（小红书备选爬虫，可选）")

    # 配置合法性
    try:
        cfg_path = os.path.join(SKILL_DIR, "config.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            json.load(f)
        print(f"[config.json] {OK}")
    except Exception as e:
        print(f"[config.json] {MISS} 解析失败：{e}")
        ok = False

    print("=" * 50)
    print("外部爬虫部署提示（按需）：")
    print("  - 微博  ：git clone https://github.com/NanmiCoder/MediaCrawler（或 weiboSpider）")
    print("  - 小红书：git clone https://github.com/cv-cat/Spider_XHS")
    print("=" * 50)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(check())
