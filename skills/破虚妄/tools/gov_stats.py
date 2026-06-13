"""
国家统计局等开放数据查询工具
================================
封装国家统计局(NBS)公开API，服务于事实核查技能(fact-check)。
覆盖全国/分省/城市的月度、季度、年度宏观数据。

API 来源:
- 旧版 easyquery: https://data.stats.gov.cn/easyquery.htm
- 新版 V2.0:     https://data.stats.gov.cn/dg/website/publicrelease/web/external

使用示例:
    from gov_stats import NBSClient

    client = NBSClient()
    data = client.query("CPI", freq="monthly", start="202501", end="202503")
    annual = client.query("GDP", freq="annual", region="110000")
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from functools import lru_cache
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

EASYQUERY_URL = "https://data.stats.gov.cn/easyquery.htm"
NEW_API_BASE = "https://data.stats.gov.cn/dg/website/publicrelease/web/external"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": "https://data.stats.gov.cn/",
}

# 全国地区代码
REGION_NATIONAL = "000000"

# ---------------------------------------------------------------------------
# 预设指标 — 常用统计指标代码 (旧版 easyquery zb 维度)
# ---------------------------------------------------------------------------

INDICATORS: dict[str, dict[str, str]] = {
    # 价格
    "CPI": {
        "code": "A010101",
        "name": "居民消费价格指数(上年同月=100)",
        "freq": "monthly",
    },
    "CPI_annual": {
        "code": "A090201",
        "name": "居民消费价格指数(上年=100)",
        "freq": "annual",
    },
    "PPI": {
        "code": "A010201",
        "name": "工业生产者出厂价格指数(上年同月=100)",
        "freq": "monthly",
    },
    # 国民经济核算
    "GDP": {
        "code": "A020102",
        "name": "国内生产总值(现价)",
        "freq": "quarterly",
    },
    "GDP_per_capita": {
        "code": "A020106",
        "name": "人均国内生产总值",
        "freq": "annual",
    },
    "GDP_index": {
        "code": "A020101",
        "name": "国内生产总值指数(上年=100)",
        "freq": "quarterly",
    },
    # 就业
    "unemployment": {
        "code": "A0E01",
        "name": "城镇调查失业率",
        "freq": "monthly",
    },
    # 消费与零售
    "retail_sales": {
        "code": "A0701",
        "name": "社会消费品零售总额",
        "freq": "monthly",
    },
    # 投资
    "fixed_investment": {
        "code": "A0601",
        "name": "固定资产投资(不含农户)",
        "freq": "monthly",
    },
    "real_estate_investment": {
        "code": "A060101",
        "name": "房地产开发投资额",
        "freq": "monthly",
    },
    # 外贸
    "import_export": {
        "code": "A0801",
        "name": "进出口总额",
        "freq": "monthly",
    },
    # 货币
    "money_supply": {
        "code": "A0D01",
        "name": "货币供应量(M0/M1/M2)",
        "freq": "monthly",
    },
    # PMI
    "PMI": {
        "code": "A0B01",
        "name": "制造业采购经理指数",
        "freq": "monthly",
    },
    # 人口
    "population": {
        "code": "A030101",
        "name": "年末总人口",
        "freq": "annual",
    },
    "birth_rate": {
        "code": "A030102",
        "name": "人口出生率",
        "freq": "annual",
    },
}

# 数据库代码 → 中文说明
DBCODE_MAP: dict[str, str] = {
    "hgnd": "全国年度",
    "hgyd": "全国月度",
    "hgjd": "全国季度",
    "fsnd": "分省年度",
    "fsyd": "分省月度",
    "fsjd": "分省季度",
    "csnd": "城市年度",
    "csyd": "城市月度",
    "csjd": "城市季度",
}

# 频率 → 默认 dbcode (全国)
FREQ_DBCODE: dict[str, str] = {
    "annual": "hgnd",
    "monthly": "hgyd",
    "quarterly": "hgjd",
}

# 频率 → 省份 dbcode
FREQ_DBCODE_PROV: dict[str, str] = {
    "annual": "fsnd",
    "monthly": "fsyd",
    "quarterly": "fsjd",
}

# 频率 → 城市 dbcode
FREQ_DBCODE_CITY: dict[str, str] = {
    "annual": "csnd",
    "monthly": "csyd",
    "quarterly": "csjd",
}


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------


class NBSClient:
    """国家统计局数据查询客户端。

    封装 easyquery 与新版 V2.0 接口，自动管理会话与重试。

    Usage::

        client = NBSClient()
        data = client.query("CPI", freq="monthly", start="202501", end="202503")
        df   = client.to_dataframe(data)
    """

    def __init__(self, timeout: int = 15, retries: int = 2) -> None:
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        # 先访问首页获取 Cookie
        try:
            self.session.get(
                "https://data.stats.gov.cn/",
                timeout=self.timeout,
            )
        except requests.RequestException:
            pass

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def query(
        self,
        indicator: str,
        freq: str = "monthly",
        region: str = REGION_NATIONAL,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> dict[str, Any]:
        """查询指标数据。

        Args:
            indicator: 指标名 (如 "CPI", "GDP") 或指标代码 (如 "A010101")。
            freq: 频率 — "monthly" / "quarterly" / "annual"。
            region: 地区代码，全国为 "000000"，省份为 6 位行政区划代码。
            start: 起始时间。月度="YYYYMM", 季度="YYYYQ", 年度="YYYY"。
            end: 截止时间，格式同 start。若不传则仅查 start 所在期。

        Returns:
            {
                "indicator": "CPI",
                "name": "居民消费价格指数...",
                "freq": "monthly",
                "region": "000000",
                "unit": "%",
                "data": [{"period": "202501", "value": 100.5}, ...]
            }
        """
        code = self._resolve_code(indicator)
        meta = INDICATORS.get(indicator, INDICATORS.get(code, {}))
        dbcode = self._pick_dbcode(freq, region)

        if end is None:
            end = start if start else self._latest_period(freq)

        params = self._build_easyquery_params(dbcode, code, region, start, end)
        raw = self._request(EASYQUERY_URL, params=params)

        points = self._parse_easyquery(raw)
        return {
            "indicator": indicator,
            "name": meta.get("name", code),
            "freq": freq,
            "region": region,
            "unit": self._guess_unit(raw),
            "data": points,
        }

    def query_province(
        self,
        indicator: str,
        freq: str = "monthly",
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """按省份查询，返回各省数据列表。"""
        results: list[dict[str, Any]] = []
        # 遍历部分常见省份代码
        for code, name in PROVINCE_CODES.items():
            try:
                r = self.query(indicator, freq=freq, region=code, start=start, end=end)
                r["province"] = name
                results.append(r)
                time.sleep(0.3)  # 限速
            except Exception:
                logger.warning("跳过省份 %s", name)
        return results

    def search(self, keyword: str) -> list[dict[str, str]]:
        """新版 API 关键词搜索，返回匹配的数据集列表。

        Returns:
            [{"cid": "...", "name": "...", "description": "..."}, ...]
        """
        url = f"{NEW_API_BASE}/external/query"
        params: dict[str, Any] = {
            "keyword": keyword,
            "pageNum": 1,
            "pageSize": 20,
        }
        raw = self._request(url, params=params)
        items = []
        try:
            records = raw.get("data", {}).get("records", [])
            for r in records:
                items.append(
                    {
                        "cid": r.get("id", ""),
                        "name": r.get("name", r.get("title", "")),
                        "description": r.get("description", ""),
                    }
                )
        except Exception:
            pass
        return items

    def get_category_tree(self, parent_id: str = "") -> list[dict[str, Any]]:
        """新版 API 获取分类树 (用于浏览可用数据集)。"""
        url = f"{NEW_API_BASE}/new/queryIndexTreeAsync"
        params = {"id": parent_id} if parent_id else {}
        raw = self._request(url, params=params)
        nodes = []
        try:
            for n in raw.get("data", []):
                nodes.append(
                    {
                        "id": n.get("id", ""),
                        "name": n.get("name", ""),
                        "has_children": n.get("hasChild", False),
                        "cid": n.get("cid", ""),
                    }
                )
        except Exception:
            pass
        return nodes

    def query_v2(
        self,
        cid: str,
        indicator_ids: list[str],
        start: str,
        end: str,
        freq_suffix: str = "MM",
    ) -> dict[str, Any]:
        """新版 API 批量查询 (POST)。

        Args:
            cid: 数据集 ID (来自 search 或分类树)。
            indicator_ids: 指标 ID 列表 (来自 queryIndicatorsByCid)。
            start: 起始时间，如 "202501"。
            end: 截止时间，如 "202503"。
            freq_suffix: "MM"(月) / "SS"(季) / "YY"(年)。
        """
        url = f"{NEW_API_BASE}/getEsDataByCidAndDt"
        payload = {
            "cid": cid,
            "dt": f"{start}{freq_suffix}-{end}{freq_suffix}",
            "indicatorIds": indicator_ids,
        }
        raw = self._request(url, method="POST", json_data=payload)
        return self._parse_v2(raw, indicator_ids)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _resolve_code(self, indicator: str) -> str:
        """将指标名或代码统一解析为 zbcode。"""
        if indicator in INDICATORS:
            return INDICATORS[indicator]["code"]
        # 可能是直接传入的代码
        return indicator

    def _pick_dbcode(self, freq: str, region: str) -> str:
        if region == REGION_NATIONAL:
            return FREQ_DBCODE.get(freq, "hgyd")
        if len(region) == 6:
            return FREQ_DBCODE_PROV.get(freq, "fsyd")
        return FREQ_DBCODE_CITY.get(freq, "csyd")

    @staticmethod
    def _latest_period(freq: str) -> str:
        """返回当前最近的统计周期，作为 end 的默认值。"""
        # 统计局数据通常滞后 1-2 个月，此处使用近似值
        now = time.localtime()
        y, m = now.tm_year, now.tm_mon
        # 回退两个月覆盖常见延迟
        m -= 2
        if m <= 0:
            m += 12
            y -= 1
        if freq == "monthly":
            return f"{y}{m:02d}"
        if freq == "quarterly":
            q = (m - 1) // 3 + 1
            return f"{y}Q{q}"
        return str(y)

    def _build_easyquery_params(
        self,
        dbcode: str,
        zbcode: str,
        region: str,
        start: Optional[str],
        end: Optional[str],
    ) -> dict[str, Any]:
        sj_str = f"{start}-{end}" if (start and end) else (start or end or "")
        dfwds = [
            {"wdcode": "zb", "valuecode": zbcode},
            {"wdcode": "sj", "valuecode": sj_str},
        ]
        if region and region != REGION_NATIONAL:
            dfwds.append({"wdcode": "reg", "valuecode": region})
        return {
            "m": "QueryData",
            "dbcode": dbcode,
            "rowcode": "zb",
            "colcode": "sj",
            "wds": "[]",
            "dfwds": json.dumps(dfwds, ensure_ascii=False),
            "k1": str(int(time.time() * 1000)),
        }

    def _request(
        self,
        url: str,
        params: Optional[dict[str, Any]] = None,
        method: str = "GET",
        json_data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        last_exc: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                if method == "POST":
                    resp = self.session.post(
                        url,
                        json=json_data,
                        timeout=self.timeout,
                    )
                else:
                    resp = self.session.get(
                        url,
                        params=params,
                        timeout=self.timeout,
                    )
                resp.raise_for_status()
                # 部分接口返回 text/html 包裹的 JSON
                text = resp.text.strip()
                if text.startswith("{"):
                    return json.loads(text)
                if text.startswith("jQuery"):
                    # easyquery 有时包裹在 JSONP 中
                    return self._unwrap_jsonp(text)
                return {}
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(1 * (attempt + 1))
        raise RuntimeError(
            f"请求失败 (已重试 {self.retries} 次): {last_exc}"
        ) from last_exc

    @staticmethod
    def _unwrap_jsonp(text: str) -> dict[str, Any]:
        """剥离 JSONP 包裹，如 jQueryXXXX(...)"""
        start = text.find("(")
        end = text.rfind(")")
        if start != -1 and end != -1:
            inner = text[start + 1 : end]
            return json.loads(inner)
        return {}

    @staticmethod
    def _parse_easyquery(raw: dict[str, Any]) -> list[dict[str, Any]]:
        """从 easyquery 响应中提取时序数据。"""
        points: list[dict[str, Any]] = []
        try:
            wdnodes = raw.get("returndata", {}).get("wdnodes", [])
            datanodes = raw.get("returndata", {}).get("datanodes", [])

            # 构建 sj(时间) → value 映射
            sj_map: dict[str, str] = {}
            for wd in wdnodes:
                if wd.get("wdcode") == "sj":
                    for node in wd.get("nodes", []):
                        sj_map[node.get("code", "")] = node.get("cname", node.get("code", ""))

            for dn in datanodes:
                code = dn.get("code", "")
                # code 格式类似 "zb.A010101_sj.202501" → 拆出时间
                parts = code.split("_sj.")
                period = parts[1] if len(parts) == 2 else code
                val = dn.get("data", {}).get("strdata", "")
                try:
                    val_num = float(val)
                except (ValueError, TypeError):
                    val_num = None
                points.append({"period": period, "value": val_num, "raw": val})
        except Exception:
            pass
        return points

    @staticmethod
    def _parse_v2(
        raw: dict[str, Any], indicator_ids: list[str]
    ) -> dict[str, Any]:
        """新版 API 响应解析。"""
        result: dict[str, Any] = {"indicators": {}}
        try:
            data = raw.get("data", {})
            for iid in indicator_ids:
                series = data.get(iid, [])
                result["indicators"][iid] = [
                    {"period": p.get("dt", ""), "value": p.get("value")}
                    for p in series
                ]
        except Exception:
            pass
        return result

    @staticmethod
    def _guess_unit(raw: dict[str, Any]) -> str:
        """尝试从元数据中解析单位。"""
        try:
            for wd in raw.get("returndata", {}).get("wdnodes", []):
                if wd.get("wdcode") == "zb":
                    unit_node = wd.get("nodes", [{}])[0] if wd.get("nodes") else {}
                    return unit_node.get("unit", "")
        except Exception:
            pass
        return ""

    @staticmethod
    def to_dataframe(query_result: dict[str, Any]) -> "Any":
        """转为 pandas DataFrame (需要安装 pandas)。"""
        try:
            import pandas as pd
        except ImportError:
            raise ImportError("需要安装 pandas: pip install pandas")
        rows = query_result.get("data", [])
        return pd.DataFrame(rows)

    @staticmethod
    def list_indicators() -> dict[str, str]:
        """列出所有预设指标及其说明。"""
        return {k: v["name"] for k, v in INDICATORS.items()}


# ---------------------------------------------------------------------------
# 省份代码速查
# ---------------------------------------------------------------------------

PROVINCE_CODES: dict[str, str] = {
    "110000": "北京",
    "120000": "天津",
    "130000": "河北",
    "140000": "山西",
    "150000": "内蒙古",
    "210000": "辽宁",
    "220000": "吉林",
    "230000": "黑龙江",
    "310000": "上海",
    "320000": "江苏",
    "330000": "浙江",
    "340000": "安徽",
    "350000": "福建",
    "360000": "江西",
    "370000": "山东",
    "410000": "河南",
    "420000": "湖北",
    "430000": "湖南",
    "440000": "广东",
    "450000": "广西",
    "460000": "海南",
    "500000": "重庆",
    "510000": "四川",
    "520000": "贵州",
    "530000": "云南",
    "540000": "西藏",
    "610000": "陕西",
    "620000": "甘肃",
    "630000": "青海",
    "640000": "宁夏",
    "650000": "新疆",
}


# ---------------------------------------------------------------------------
# 便捷函数 (模块级)
# ---------------------------------------------------------------------------


def query(
    indicator: str,
    freq: str = "monthly",
    region: str = REGION_NATIONAL,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> dict[str, Any]:
    """模块级快捷查询，自动创建 NBSClient 实例。"""
    client = NBSClient()
    return client.query(indicator, freq=freq, region=region, start=start, end=end)


def search(keyword: str) -> list[dict[str, str]]:
    """模块级快捷搜索。"""
    return NBSClient().search(keyword)
