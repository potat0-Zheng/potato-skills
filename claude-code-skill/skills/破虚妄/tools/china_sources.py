"""
中国本土核查源查询工具
========================
封装中国大陆可用的公开核查与信息验证源，服务于事实核查技能。
所有 API 均基于中国大陆可正常访问的公开端点，无需翻墙。

覆盖范围:
- 中国互联网联合辟谣平台 (piyao.org.cn)
- 微博搜索 (关键词搜索 + 用户搜索)
- 企查查公开工商信息
- 澎湃新闻搜索 (thepaper.cn)

使用示例:
    from china_sources import ChinaVerifyClient

    client = ChinaVerifyClient()
    # 搜索辟谣信息
    results = client.piyao_search("某传闻内容")
    # 搜索微博
    posts = client.weibo_search("关键词")
    # 核查企业信息
    info = client.company_lookup("OPPO广东移动通信有限公司")
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote, urlencode

import requests

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,application/xhtml+xml,*/*",
}

# ---------------------------------------------------------------------------
# 辟谣平台
# ---------------------------------------------------------------------------

PIYAO_SEARCH_URL = "https://www.piyao.org.cn/search.htm"


class ChinaVerifyClient:
    """中国本土核查源查询客户端。

    Usage::

        client = ChinaVerifyClient()
        debunk = client.piyao_search("某传言")
        weibo  = client.weibo_search("OPPO 母亲节")
        corp   = client.company_lookup("OPPO广东移动通信有限公司")
    """

    def __init__(self, timeout: int = 15, retries: int = 2) -> None:
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    # ------------------------------------------------------------------
    # 1. 辟谣平台搜索
    # ------------------------------------------------------------------

    def piyao_search(self, keyword: str) -> dict[str, Any]:
        """在中国互联网联合辟谣平台搜索相关辟谣信息。

        Returns:
            {
                "keyword": "...",
                "found": True/False,
                "results": [{"title": "...", "url": "...", "date": "...", "summary": "..."}]
            }
        """
        results: list[dict[str, str]] = []
        try:
            params = {"keywords": keyword}
            resp = self.session.get(
                PIYAO_SEARCH_URL,
                params=params,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            # 从搜索结果页提取辟谣条目
            text = resp.text
            # 简单 HTML 提取（辟谣平台页面结构稳定）
            items = re.findall(
                r'<a[^>]*href="([^"]*)"[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</a>',
                text,
                re.DOTALL,
            )
            for url, title in items[:10]:
                clean_title = re.sub(r"<[^>]+>", "", title).strip()
                if clean_title:
                    results.append({
                        "title": clean_title,
                        "url": (
                            url if url.startswith("http")
                            else f"https://www.piyao.org.cn{url}"
                        ),
                        "date": "",
                        "summary": "",
                    })
        except requests.RequestException as exc:
            logger.warning("辟谣平台搜索失败: %s", exc)

        return {
            "keyword": keyword,
            "source": "中国互联网联合辟谣平台",
            "found": len(results) > 0,
            "results": results,
        }

    # ------------------------------------------------------------------
    # 2. 微博搜索
    # ------------------------------------------------------------------

    def weibo_search(
        self, keyword: str, search_type: str = "keyword", limit: int = 20
    ) -> dict[str, Any]:
        """通过微博公开搜索接口检索相关帖子。

        Args:
            keyword: 搜索关键词。
            search_type: "keyword" 综合搜索 / "user" 用户搜索。
            limit: 最大返回条数。

        Returns:
            {
                "keyword": "...",
                "results": [{"content": "...", "user": "...", "time": "...", "url": "..."}]
            }
        """
        results: list[dict[str, str]] = []
        try:
            encoded = quote(keyword)
            url = f"https://s.weibo.com/weibo?q={encoded}&typeall=1&suball=1"
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            text = resp.text

            # 从搜索页提取微博卡片信息
            cards = re.findall(
                r'<p[^>]*class="txt"[^>]*>(.*?)</p>.*?<a[^>]*class="name"[^>]*>(.*?)</a>.*?<a[^>]*class="date"[^>]*>(.*?)</a>',
                text,
                re.DOTALL,
            )
            for content, user, date_str in cards[:limit]:
                clean_content = re.sub(r"<[^>]+>", "", content).strip()
                clean_user = re.sub(r"<[^>]+>", "", user).strip()
                if clean_content:
                    results.append({
                        "content": clean_content,
                        "user": clean_user,
                        "time": date_str.strip(),
                        "url": "",
                    })
        except requests.RequestException as exc:
            logger.warning("微博搜索失败: %s", exc)

        return {
            "keyword": keyword,
            "source": "微博公开搜索",
            "results": results,
        }

    def weibo_user_posts(
        self, nickname: str, limit: int = 20
    ) -> dict[str, Any]:
        """搜索特定用户的微博帖子。

        Args:
            nickname: 微博昵称。
            limit: 最大返回条数。
        """
        try:
            encoded = quote(nickname)
            url = f"https://s.weibo.com/user?q={encoded}"
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            # 定位用户主页
            uid_match = re.search(r'uid=(\d+)', resp.text)
            if not uid_match:
                return {
                    "nickname": nickname,
                    "source": "微博用户搜索",
                    "found": False,
                    "posts": [],
                }
            uid = uid_match.group(1)
            profile_url = f"https://weibo.com/u/{uid}"
            profile_resp = self.session.get(
                f"https://weibo.com/ajax/statuses/mymblog?uid={uid}&page=1&feature=0",
                headers={
                    **HEADERS,
                    "Referer": profile_url,
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=self.timeout,
            )
            data = profile_resp.json()
            posts = []
            for item in data.get("data", {}).get("list", [])[:limit]:
                text_raw = item.get("text_raw", "")
                created = item.get("created_at", "")
                posts.append({
                    "content": text_raw[:200],
                    "time": created,
                    "url": f"https://weibo.com/{uid}/{item.get('mid', '')}",
                })
            return {
                "nickname": nickname,
                "source": "微博用户主页",
                "found": True,
                "posts": posts,
            }
        except requests.RequestException as exc:
            logger.warning("微博用户搜索失败: %s", exc)
            return {
                "nickname": nickname,
                "source": "微博用户搜索",
                "found": False,
                "posts": [],
            }

    # ------------------------------------------------------------------
    # 3. 企业工商信息查询 (企查查公开数据)
    # ------------------------------------------------------------------

    def company_lookup(self, company_name: str) -> dict[str, Any]:
        """查询企业公开工商信息（通过企查查/公开渠道）。

        Returns:
            {
                "company": "...",
                "found": True/False,
                "info": {
                    "legal_person": "...",
                    "registered_capital": "...",
                    "established": "...",
                    "status": "...",
                    "address": "..."
                }
            }
        """
        try:
            encoded = quote(company_name)
            url = f"https://www.qcc.com/web/search?key={encoded}"
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            text = resp.text

            # 从搜索结果页提取基础工商信息
            info: dict[str, str] = {}
            patterns = {
                "legal_person": r'法定代表人[：:]\s*([^<\n]+)',
                "registered_capital": r'注册资本[：:]\s*([^<\n]+)',
                "established": r'成立日期[：:]\s*([^<\n]+)',
                "status": r'经营状态[：:]\s*([^<\n]+)',
            }
            for key, pattern in patterns.items():
                match = re.search(pattern, text)
                if match:
                    info[key] = match.group(1).strip()

            return {
                "company": company_name,
                "source": "企查查公开数据",
                "found": len(info) > 0,
                "info": info,
            }
        except requests.RequestException as exc:
            logger.warning("企业查询失败: %s", exc)
            return {
                "company": company_name,
                "source": "企查查公开数据",
                "found": False,
                "info": {},
            }

    def company_verify_executive(
        self, company_name: str, executive_name: str
    ) -> dict[str, Any]:
        """验证某公司的高管任职关系。

        Returns:
            {
                "company": "...",
                "executive": "...",
                "matched": True/False,
                "position": "..." if matched else None
            }
        """
        info = self.company_lookup(company_name)
        result: dict[str, Any] = {
            "company": company_name,
            "executive": executive_name,
            "matched": False,
            "position": None,
        }
        if info.get("found"):
            legal = info.get("info", {}).get("legal_person", "")
            if executive_name in legal:
                result["matched"] = True
                result["position"] = "法定代表人"
        return result

    # ------------------------------------------------------------------
    # 4. 澎湃新闻搜索 (权威新闻源直接检索)
    # ------------------------------------------------------------------

    def thepaper_search(
        self, keyword: str, limit: int = 20
    ) -> dict[str, Any]:
        """搜索澎湃新闻（国内有事实核查栏目的权威媒体）。

        Returns:
            {"keyword": "...", "results": [{"title": "...", "url": "...", "date": "..."}]}
        """
        results: list[dict[str, str]] = []
        try:
            encoded = quote(keyword)
            url = f"https://www.thepaper.cn/searchResult?searchKeyword={encoded}"
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            text = resp.text

            # 提取搜索结果
            items = re.findall(
                r'<a[^>]*href="([^"]*)"[^>]*>.*?<h3[^>]*>(.*?)</h3>.*?<span[^>]*>(.*?)</span>',
                text,
                re.DOTALL,
            )
            for link, title, date_str in items[:limit]:
                clean_title = re.sub(r"<[^>]+>", "", title).strip()
                if clean_title:
                    results.append({
                        "title": clean_title,
                        "url": link if link.startswith("http") else f"https://www.thepaper.cn{link}",
                        "date": date_str.strip(),
                    })
        except requests.RequestException as exc:
            logger.warning("澎湃新闻搜索失败: %s", exc)

        return {
            "keyword": keyword,
            "source": "澎湃新闻",
            "results": results,
        }

    # ------------------------------------------------------------------
    # 5. 批量核实 — 根据断言类型自动选择来源
    # ------------------------------------------------------------------

    def verify(
        self, claim_text: str, claim_type: str = "general"
    ) -> dict[str, Any]:
        """根据断言类型自动选择核查源。

        Args:
            claim_text: 待查断言文本。
            claim_type: "rumor" / "enterprise" / "social_media" / "general"

        Returns:
            聚合的多源核查结果。
        """
        result: dict[str, Any] = {
            "claim": claim_text,
            "claim_type": claim_type,
            "sources_checked": [],
            "findings": {},
        }

        if claim_type in ("rumor", "general"):
            debunk = self.piyao_search(claim_text)
            result["findings"]["piyao"] = debunk
            result["sources_checked"].append("piyao.org.cn")

            news = self.thepaper_search(claim_text)
            result["findings"]["thepaper"] = news
            result["sources_checked"].append("thepaper.cn")

        if claim_type in ("social_media", "general"):
            weibo = self.weibo_search(claim_text)
            result["findings"]["weibo"] = weibo
            result["sources_checked"].append("weibo.com")

        if claim_type == "enterprise":
            corp = self.company_lookup(claim_text)
            result["findings"]["company"] = corp
            result["sources_checked"].append("qcc.com")

        return result


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------


def piyao(keyword: str) -> dict[str, Any]:
    """快捷辟谣搜索。"""
    return ChinaVerifyClient().piyao_search(keyword)


def weibo(keyword: str) -> dict[str, Any]:
    """快捷微博搜索。"""
    return ChinaVerifyClient().weibo_search(keyword)


def company(name: str) -> dict[str, Any]:
    """快捷企业查询。"""
    return ChinaVerifyClient().company_lookup(name)
