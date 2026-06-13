"""
Google Fact Check Tools 查询工具
=================================
封装 Google Fact Check Tools API (https://toolbox.google.com/factcheck/api)，
服务于事实核查技能。用于：
- 查询已有的事实核查记录（聚合全球多家核查机构）
- 避免重复核查已被权威机构验证过的内容
- 获取多来源核查结论作为交叉参考

API: 免费使用，无需 API Key（有限速）。可选配置 API Key 提高配额。

使用示例:
    from google_factcheck import FactCheckClient

    client = FactCheckClient()
    results = client.search("中国2023年GDP增长5.2%")
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://contentfactchecktools.googleapis.com/v1alpha1/claims:search"

HEADERS = {
    "User-Agent": "fact-check-skill/1.0 (academic-use)",
    "Accept": "application/json",
}

# 已知的可信核查机构 (部分)
KNOWN_PUBLISHERS = [
    "FactCheck.org",
    "PolitiFact",
    "Snopes",
    "AFP Fact Check",
    "Reuters Fact Check",
    "Full Fact",
    "The Washington Post Fact Checker",
    "Associated Press",
    "USA Today",
    "Lead Stories",
    "Science Feedback",
    "Health Feedback",
    "Estadão Verifica",
    "Boom",
    "Factly",
]


class FactCheckClient:
    """Google Fact Check Tools 查询客户端。

    Usage::

        client = FactCheckClient()
        results = client.search("某个声称需要核查")
        report = client.make_report("某个声称需要核查")
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: int = 15,
        retries: int = 2,
    ) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        language: str = "zh",
        page_size: int = 20,
    ) -> dict[str, Any]:
        """搜索事实核查记录。

        Args:
            query: 待核查的文本或声明。
            language: 语言代码，"zh" 中文 / "en" 英文 / "" 不限。
            page_size: 返回条目数。

        Returns:
            {
                "query": "...",
                "total_results": 5,
                "reviews": [
                    {
                        "publisher": "AFP Fact Check",
                        "title": "...",
                        "rating": "False",
                        "url": "https://...",
                        "date": "2024-01-15",
                        "excerpt": "..."
                    },
                    ...
                ]
            }
        """
        params: dict[str, Any] = {
            "query": query,
            "pageSize": min(page_size, 100),
        }
        if language:
            params["languageCode"] = language
        if self.api_key:
            params["key"] = self.api_key

        raw = self._request(API_BASE, params=params)
        if raw is None:
            return {"query": query, "total_results": 0, "reviews": []}

        reviews = self._parse(raw)
        return {
            "query": query,
            "total_results": len(reviews),
            "reviews": reviews,
        }

    def make_report(
        self, claim: str, language: str = "zh"
    ) -> dict[str, Any]:
        """为某声明生成核查摘要，专为 skill 报告使用。

        Returns:
            {
                "claim": "...",
                "previously_checked": True/False,
                "consensus": "True" | "False" | "Mixed" | "Unknown",
                "publishers": [...],
                "summary": "已有 3 家机构核查，均判定为虚假...",
                "details": [...]
            }
        """
        result = self.search(claim, language=language)
        result_dict: dict[str, Any] = {
            "claim": claim,
            "previously_checked": False,
            "consensus": "Unknown",
            "publishers": [],
            "summary": "",
            "details": [],
        }

        reviews = result.get("reviews", [])
        if not reviews:
            result_dict["summary"] = "未找到已有的事实核查记录，需从其他来源进行首次核查。"
            return result_dict

        result_dict["previously_checked"] = True
        publishers_seen: set[str] = set()

        ratings: list[str] = []
        for r in reviews:
            publisher = r.get("publisher", "未知")
            rating = r.get("rating", "未评定")
            publishers_seen.add(publisher)
            ratings.append(rating)
            result_dict["details"].append({
                "publisher": publisher,
                "title": r.get("title", ""),
                "rating": rating,
                "url": r.get("url", ""),
                "date": r.get("date", ""),
            })

        result_dict["publishers"] = sorted(publishers_seen)

        # 判断共识
        true_like = {"true", "mostly true", "correct", "accurate", "真实", "正确"}
        false_like = {"false", "mostly false", "incorrect", "pants on fire", "fake", "虚假", "不实", "谣言"}
        t_count, f_count = 0, 0
        for r in ratings:
            r_lower = r.lower().strip()
            if any(t in r_lower for t in true_like):
                t_count += 1
            elif any(f in r_lower for f in false_like):
                f_count += 1

        total_rated = t_count + f_count
        if total_rated > 0:
            if t_count == total_rated:
                result_dict["consensus"] = "True"
            elif f_count == total_rated:
                result_dict["consensus"] = "False"
            else:
                result_dict["consensus"] = "Mixed"

        result_dict["summary"] = (
            f"已有 {len(publishers_seen)} 家核查机构就此声明发布过报告。"
            f"共识: {result_dict['consensus']}。"
        )
        return result_dict

    def search_multi_lang(self, claim: str) -> dict[str, Any]:
        """多语言搜索：中英文并行搜索，汇总结果。"""
        zh_result = self.search(claim, language="zh")
        en_result = self.search(claim, language="en")
        all_reviews = (
            zh_result.get("reviews", []) + en_result.get("reviews", [])
        )
        # 去重 (按 URL)
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for r in all_reviews:
            url = r.get("url", "")
            if url and url not in seen:
                seen.add(url)
                deduped.append(r)
        return {
            "query": claim,
            "total_results": len(deduped),
            "reviews": deduped,
        }

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _request(
        self, url: str, params: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        last_exc: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))
            except Exception as exc:
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))
        logger.warning("Google FactCheck API 请求失败: %s", last_exc)
        return None

    @staticmethod
    def _parse(raw: dict[str, Any]) -> list[dict[str, Any]]:
        reviews: list[dict[str, Any]] = []
        try:
            for claim_review in raw.get("claims", []):
                review = claim_review.get("claimReview", {})
                reviews.append({
                    "publisher": (
                        review.get("publisher", {}).get("name", "未知")
                    ),
                    "title": review.get("title", ""),
                    "rating": review.get("textualRating", ""),
                    "url": review.get("url", ""),
                    "date": review.get("reviewDate", ""),
                    "excerpt": claim_review.get("text", ""),
                })
        except Exception:
            pass
        return reviews


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------


def search(query: str, language: str = "zh") -> dict[str, Any]:
    """模块级快捷搜索。"""
    return FactCheckClient().search(query, language=language)


def make_report(claim: str) -> dict[str, Any]:
    """模块级快捷报告。"""
    return FactCheckClient().make_report(claim)
