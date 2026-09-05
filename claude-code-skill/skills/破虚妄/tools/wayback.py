"""
Internet Archive (Wayback Machine) 查询工具
============================================
封装 Wayback Machine & CDX API，服务于事实核查技能。
用于：
- 验证某网页在特定时间点是否存在
- 确认内容是否曾被修改/删除
- 获取历史快照辅助判断信息真实性

API: 完全免费，无需注册。

使用示例:
    from wayback import WaybackClient

    client = WaybackClient()
    # 检查某 URL 是否有存档
    snap = client.check_url("https://example.com/article")
    # 获取快照内容
    html = client.get_snapshot("https://example.com/article", "20240101000000")
"""

from __future__ import annotations

import datetime
import json
import logging
import time
from typing import Any, Optional
from urllib.parse import quote, urlparse

import requests

logger = logging.getLogger(__name__)

# API 端点
AVAILABILITY_API = "https://archive.org/wayback/available"
CDX_API = "https://web.archive.org/cdx/search/cdx"
SAVE_API = "https://web.archive.org/save"

HEADERS = {
    "User-Agent": "fact-check-skill/1.0 (academic-use)",
    "Accept": "application/json",
}


class WaybackClient:
    """Internet Archive Wayback Machine 查询客户端。

    支持三种操作：
    1. check_url — 查询某 URL 是否被存档
    2. get_snapshot — 获取某时间点的页面快照
    3. list_snapshots — 列出所有快照时间点
    """

    def __init__(self, timeout: int = 30, retries: int = 2) -> None:
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def check_url(
        self, url: str, timestamp: Optional[str] = None
    ) -> dict[str, Any]:
        """检查某 URL 在 Wayback Machine 中是否被存档。

        Args:
            url: 待检查的网页 URL。
            timestamp: 目标时间 (YYYYMMDDHHMMSS)，不传则返回最近的快照。

        Returns:
            {
                "url": "...",
                "archived": True/False,
                "snapshot_url": "https://web.archive.org/web/...",
                "timestamp": "20240101000000",
                "status": "200"
            }
        """
        params: dict[str, Any] = {"url": url}
        if timestamp:
            params["timestamp"] = timestamp

        raw = self._request(AVAILABILITY_API, params=params)
        result = {
            "url": url,
            "archived": False,
            "snapshot_url": None,
            "timestamp": None,
            "status": None,
        }

        if raw is None:
            return result

        snapshots = raw.get("archived_snapshots", {})
        closest = snapshots.get("closest", {}) if snapshots else {}
        if closest and closest.get("available"):
            result["archived"] = True
            result["snapshot_url"] = closest.get("url", "")
            result["timestamp"] = closest.get("timestamp", "")
            result["status"] = closest.get("status", "")
        return result

    def get_snapshot(
        self, url: str, timestamp: Optional[str] = None
    ) -> dict[str, Any]:
        """获取页面快照的 HTML 内容。

        Args:
            url: 原始 URL。
            timestamp: 目标时间，不传取最新。

        Returns:
            {
                "url": "...",
                "snapshot_url": "...",
                "timestamp": "...",
                "content": "<html...>",  # 快照 HTML 原文
                "content_length": 12345
            }
        """
        snap_info = self.check_url(url, timestamp)
        if not snap_info["archived"]:
            return {
                "url": url,
                "snapshot_url": None,
                "timestamp": None,
                "content": None,
                "content_length": 0,
                "error": "该 URL 未被存档",
            }

        try:
            resp = self.session.get(
                snap_info["snapshot_url"],
                timeout=self.timeout,
                headers={"User-Agent": HEADERS["User-Agent"]},
            )
            resp.raise_for_status()
            content = resp.text
            return {
                "url": url,
                "snapshot_url": snap_info["snapshot_url"],
                "timestamp": snap_info["timestamp"],
                "content": content,
                "content_length": len(content),
            }
        except requests.RequestException as exc:
            return {
                "url": url,
                "snapshot_url": snap_info["snapshot_url"],
                "timestamp": snap_info["timestamp"],
                "content": None,
                "content_length": 0,
                "error": str(exc),
            }

    def list_snapshots(
        self,
        url: str,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """列出某 URL 的所有存档快照时间点。

        Args:
            url: 原始 URL。
            start_year: 起始年份。
            end_year: 截止年份。
            limit: 最多返回条数。

        Returns:
            [{"timestamp": "20240101000000", "status": "200", "digest": "..."}, ...]
        """
        params: dict[str, Any] = {
            "url": url,
            "output": "json",
            "limit": limit,
            "fl": "timestamp,statuscode,digest,mimetype,length",
        }
        if start_year:
            params["from"] = f"{start_year}"
        if end_year:
            params["to"] = f"{end_year}"

        data = self._request_cdx(params)
        if not data or len(data) < 2:
            return []

        # CDX 返回格式: [header_row, ...data_rows]
        headers = data[0]
        snapshots: list[dict[str, Any]] = []
        for row in data[1:]:
            entry: dict[str, Any] = {}
            for i, h in enumerate(headers):
                entry[h] = row[i] if i < len(row) else None
            entry["snapshot_url"] = (
                f"https://web.archive.org/web/{entry.get('timestamp', '')}"
                f"/{url}"
            )
            entry["timestamp_dt"] = self._parse_timestamp(
                entry.get("timestamp", "")
            )
            snapshots.append(entry)
        return snapshots

    def verify_content_existed(
        self,
        url: str,
        claim_text: str,
        timestamp: Optional[str] = None,
    ) -> dict[str, Any]:
        """验证某 URL 的快照中是否包含特定文本声明。

        典型场景：某人声称某网站在某时间点发过某报道，
        但当前页面内容已变化或被删除。

        Args:
            url: 网页 URL。
            claim_text: 声称存在的关键文本。
            timestamp: 声称的时间。

        Returns:
            {
                "url": "...",
                "claim_text": "...",
                "matched": True/False,
                "matched_snapshot": "...",
                "evidence": "..."
            }
        """
        result: dict[str, Any] = {
            "url": url,
            "claim_text": claim_text,
            "matched": False,
            "matched_timestamp": None,
            "matched_snapshot_url": None,
            "evidence": "",
        }

        if timestamp:
            # 精确查某时间点
            snap = self.get_snapshot(url, timestamp)
            if snap.get("content"):
                found = claim_text in snap["content"]
                result["matched"] = found
                result["matched_timestamp"] = snap.get("timestamp")
                result["matched_snapshot_url"] = snap.get("snapshot_url")
                if found:
                    result["evidence"] = (
                        f"快照 {snap.get('timestamp')} 中包含声称文本。"
                    )
                else:
                    result["evidence"] = (
                        f"快照 {snap.get('timestamp')} 中未找到声称文本。"
                    )
            return result

        # 未指定时间 → 在所有快照中搜索
        snapshots = self.list_snapshots(url)
        for snap in snapshots[:20]:  # 最多检查前 20 个快照
            full = self.get_snapshot(url, snap.get("timestamp"))
            if full.get("content") and claim_text in full["content"]:
                result["matched"] = True
                result["matched_timestamp"] = snap.get("timestamp")
                result["matched_snapshot_url"] = snap.get("snapshot_url")
                result["evidence"] = (
                    f"快照 {snap.get('timestamp')} 中包含声称文本。"
                )
                break
        if not result["matched"]:
            checked = min(len(snapshots), 20)
            result["evidence"] = (
                f"已检查 {checked} 个快照，均未找到声称文本。"
            )
        return result

    def domain_age(self, domain: str) -> dict[str, Any]:
        """通过 Wayback Machine 首次收录时间估算域名存在时长。

        Args:
            domain: 域名 (如 "example.com")。

        Returns:
            {
                "domain": "...",
                "first_seen": "2010-01-15T00:00:00",
                "years_online": 15
            }
        """
        url = f"http://{domain}"
        snapshots = self.list_snapshots(url, limit=1)
        result: dict[str, Any] = {
            "domain": domain,
            "first_seen": None,
            "years_online": None,
        }
        if snapshots:
            first = snapshots[0]
            dt = first.get("timestamp_dt")
            if dt:
                result["first_seen"] = dt.isoformat()
                result["years_online"] = datetime.datetime.now().year - dt.year
        return result

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _request(
        self, url: str, params: Optional[dict[str, Any]] = None
    ) -> Optional[dict[str, Any]]:
        last_exc: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, json.JSONDecodeError) as exc:
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(1 * (attempt + 1))
        logger.warning("Wayback API 请求失败: %s", last_exc)
        return None

    def _request_cdx(
        self, params: dict[str, Any]
    ) -> Optional[list[Any]]:
        last_exc: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.session.get(
                    CDX_API, params=params, timeout=self.timeout
                )
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, json.JSONDecodeError) as exc:
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(1 * (attempt + 1))
        logger.warning("CDX API 请求失败: %s", last_exc)
        return None

    @staticmethod
    def _parse_timestamp(ts: str) -> Optional[datetime.datetime]:
        """解析 Wayback 时间戳 YYYYMMDDHHMMSS → datetime。"""
        if not ts or len(ts) < 8:
            return None
        try:
            fmt = "%Y%m%d%H%M%S"
            # 补齐不足 14 位
            ts_padded = ts.ljust(14, "0")
            return datetime.datetime.strptime(ts_padded[:14], fmt)
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------


def check_url(url: str, timestamp: Optional[str] = None) -> dict[str, Any]:
    """检查 URL 是否被存档。"""
    return WaybackClient().check_url(url, timestamp)


def verify_text(url: str, claim_text: str, timestamp: Optional[str] = None) -> dict[str, Any]:
    """验证某 URL 快照中是否包含声称的文本。"""
    return WaybackClient().verify_content_existed(url, claim_text, timestamp)


def domain_first_seen(domain: str) -> dict[str, Any]:
    """查询域名首次收录时间。"""
    return WaybackClient().domain_age(domain)
