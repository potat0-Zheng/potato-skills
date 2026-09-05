"""
世界银行公开数据查询工具
==========================
封装 World Bank API (https://api.worldbank.org/v2/)，服务于事实核查技能。
用于跨国经济指标交叉验证——将 NBS 数据与世行数据进行一致性比对。

API 特性: 完全免费，无需注册，支持 REST/JSON。

使用示例:
    from world_bank import WBCLient

    client = WBCLient()
    # 查询中国 2020-2023 年 GDP (现价美元)
    data = client.query("NY.GDP.MKTP.CD", country="CN", start=2020, end=2023)
    # 交叉验证: 世行 vs NBS
    result = client.cross_validate("GDP", nbs_value=18.1e12, year=2023)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

WB_API_BASE = "https://api.worldbank.org/v2"

HEADERS = {
    "User-Agent": "fact-check-skill/1.0 (academic-use)",
    "Accept": "application/json",
}

# 常用指标 — 世行代码 : (中文名, 单位)
INDICATORS: dict[str, dict[str, str]] = {
    "NY.GDP.MKTP.CD":   {"name": "GDP (现价美元)", "unit": "美元"},
    "NY.GDP.MKTP.KD.ZG": {"name": "GDP 年增长率 (%)", "unit": "%"},
    "NY.GDP.PCAP.CD":   {"name": "人均 GDP (现价美元)", "unit": "美元"},
    "NY.GDP.PCAP.PP.KD": {"name": "人均 GDP (购买力平价, 2017年不变价)", "unit": "国际元"},
    "FP.CPI.TOTL.ZG":   {"name": "CPI 通胀率 (年通胀率)", "unit": "%"},
    "SL.UEM.TOTL.ZS":   {"name": "失业率 (ILO 估算)", "unit": "%"},
    "SP.POP.TOTL":      {"name": "人口总数", "unit": "人"},
    "SP.DYN.CBRT.IN":   {"name": "人口出生率 (每千人)", "unit": "‰"},
    "NE.EXP.GNFS.ZS":   {"name": "商品服务出口 (占 GDP %)", "unit": "%"},
    "NE.IMP.GNFS.ZS":   {"name": "商品服务进口 (占 GDP %)", "unit": "%"},
    "BN.CAB.XOKA.GD.ZS": {"name": "经常账户余额 (占 GDP %)", "unit": "%"},
    "GC.DOD.TOTL.GD.ZS": {"name": "中央政府债务 (占 GDP %)", "unit": "%"},
    "FM.LBL.BMNY.GD.ZS": {"name": "广义货币 (占 GDP %)", "unit": "%"},
    "BX.KLT.DINV.WD.GD.ZS": {"name": "FDI 净流入 (占 GDP %)", "unit": "%"},
    "IT.NET.USER.ZS":   {"name": "互联网使用率 (% 人口)", "unit": "%"},
    "SE.PRM.ENRR":      {"name": "小学入学率 (% 毛入学率)", "unit": "%"},
    "NY.GDP.MKTP.CN":   {"name": "GDP (现价本币)", "unit": "人民币"},
}

# NBS 指标 → 世行指标 对照表 (用于交叉验证)
NBS_TO_WB: dict[str, str] = {
    "GDP":             "NY.GDP.MKTP.CD",
    "GDP_index":       "NY.GDP.MKTP.KD.ZG",
    "GDP_per_capita":  "NY.GDP.PCAP.CD",
    "CPI_annual":      "FP.CPI.TOTL.ZG",
    "unemployment":    "SL.UEM.TOTL.ZS",
    "population":      "SP.POP.TOTL",
    "birth_rate":      "SP.DYN.CBRT.IN",
    "import_export":   "NE.EXP.GNFS.ZS",
}

# 常用国家代码 (ISO 3166-1 alpha-3)
COUNTRIES: dict[str, str] = {
    "CN": "中国", "US": "美国", "JP": "日本", "DE": "德国",
    "GB": "英国", "FR": "法国", "IN": "印度", "KR": "韩国",
    "RU": "俄罗斯", "BR": "巴西", "ZA": "南非", "AU": "澳大利亚",
    "CA": "加拿大", "IT": "意大利", "SG": "新加坡", "VN": "越南",
    "ID": "印尼", "MY": "马来西亚", "TH": "泰国", "PH": "菲律宾",
    "MX": "墨西哥", "TR": "土耳其", "SA": "沙特", "AE": "阿联酋",
    "WLD": "世界", "EAS": "东亚与太平洋", "OED": "OECD 成员国",
}


class WBClient:
    """世界银行数据查询客户端。

    Usage::

        client = WBClient()
        gdp = client.query("NY.GDP.MKTP.CD", country="CN", start=2020, end=2023)
        cpi = client.query("FP.CPI.TOTL.ZG", country="CN", start=2020, end=2023)
    """

    def __init__(self, timeout: int = 20, retries: int = 2) -> None:
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def query(
        self,
        indicator: str,
        country: str = "CN",
        start: int = 2010,
        end: Optional[int] = None,
        per_page: int = 100,
    ) -> dict[str, Any]:
        """查询世界银行指标数据。

        Args:
            indicator: 指标代码 (如 "NY.GDP.MKTP.CD") 或 NBS 指标名 ("GDP" 等)。
            country: ISO 3166-1 alpha-3 国家代码。
            start: 起始年份。
            end: 截止年份，默认当前年份。
            per_page: 每页返回数。

        Returns:
            {
                "indicator": "...",
                "indicator_name": "GDP (现价美元)",
                "country": "CN",
                "country_name": "中国",
                "unit": "美元",
                "data": [{"year": 2020, "value": 1.47e13}, ...]
            }
        """
        code = self._resolve_code(indicator)
        meta = INDICATORS.get(code, {"name": code, "unit": ""})
        if end is None:
            end = time.localtime().tm_year

        url = f"{WB_API_BASE}/country/{country}/indicator/{code}"
        params: dict[str, Any] = {
            "format": "json",
            "date": f"{start}:{end}",
            "per_page": per_page,
        }
        raw = self._request(url, params=params)

        # 世行返回 [metadata, data_pages]
        points = self._parse(raw)
        country_name = COUNTRIES.get(country, country)
        return {
            "indicator": code,
            "indicator_name": meta["name"],
            "country": country,
            "country_name": country_name,
            "unit": meta["unit"],
            "data": points,
        }

    def cross_validate(
        self,
        nbs_indicator: str,
        nbs_value: float,
        year: int,
        nbs_unit: str = "",
    ) -> dict[str, Any]:
        """将 NBS 数据与世行数据进行交叉验证。

        Args:
            nbs_indicator: NBS 指标名 (如 "GDP", "CPI_annual")。
            nbs_value: NBS 公布的数值。
            year: 数据年份。
            nbs_unit: NBS 数据的单位说明。

        Returns:
            {
                "nbs_indicator": "GDP",
                "nbs_value": ...,
                "wb_indicator": "NY.GDP.MKTP.CD",
                "wb_value": ...,
                "wb_year": 2023,
                "unit_note": "...",
                "discrepancy": abs(wb - nbs) / wb * 100,
                "verdict": "一致" | "小幅偏离" | "显著偏离" | "无法验证"
            }
        """
        wb_code = NBS_TO_WB.get(nbs_indicator)
        if not wb_code:
            return {
                "nbs_indicator": nbs_indicator,
                "nbs_value": nbs_value,
                "wb_indicator": None,
                "wb_value": None,
                "verdict": "无对应世行指标",
                "discrepancy": None,
            }

        result = self.query(wb_code, country="CN", start=year, end=year)
        wb_points = result.get("data", [])
        if not wb_points:
            return {
                "nbs_indicator": nbs_indicator,
                "nbs_value": nbs_value,
                "wb_indicator": wb_code,
                "wb_value": None,
                "verdict": "世行无该年度数据",
                "discrepancy": None,
            }

        wb_value = wb_points[0].get("value")
        if wb_value is None:
            return {
                "nbs_indicator": nbs_indicator,
                "nbs_value": nbs_value,
                "wb_indicator": wb_code,
                "wb_value": None,
                "verdict": "世行数据为空",
                "discrepancy": None,
            }

        discrepancy = abs(wb_value - nbs_value) / abs(wb_value) * 100 if wb_value else None

        if discrepancy is not None:
            if discrepancy < 3:
                verdict = "一致"
            elif discrepancy < 10:
                verdict = "小幅偏离 (可能因口径/汇率差异)"
            else:
                verdict = "显著偏离 — 需重点核查"
        else:
            verdict = "无法计算偏离"

        meta = INDICATORS.get(wb_code, {})
        return {
            "nbs_indicator": nbs_indicator,
            "nbs_value": nbs_value,
            "wb_indicator": wb_code,
            "wb_indicator_name": meta.get("name", wb_code),
            "wb_value": wb_value,
            "wb_year": year,
            "discrepancy_pct": round(discrepancy, 2) if discrepancy is not None else None,
            "verdict": verdict,
        }

    def list_indicators(self) -> dict[str, str]:
        """列出所有预设指标。"""
        return {k: v["name"] for k, v in INDICATORS.items()}

    def list_countries(self) -> dict[str, str]:
        """列出常用国家代码。"""
        return dict(COUNTRIES)

    def search_indicator(self, keyword: str) -> list[dict[str, str]]:
        """搜索世界银行指标 (通过 API 搜索)。"""
        url = f"{WB_API_BASE}/indicator"
        params: dict[str, Any] = {
            "format": "json",
            "search": keyword,
            "per_page": 30,
        }
        raw = self._request(url, params=params)
        results: list[dict[str, str]] = []
        try:
            for item in raw[1]:
                results.append({
                    "code": item.get("id", ""),
                    "name": item.get("name", ""),
                    "source": item.get("sourceNote", ""),
                })
        except Exception:
            pass
        return results

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _resolve_code(self, indicator: str) -> str:
        if indicator in NBS_TO_WB:
            return NBS_TO_WB[indicator]
        if indicator in INDICATORS:
            return indicator
        return indicator

    def _request(
        self, url: str, params: Optional[dict[str, Any]] = None
    ) -> list[Any]:
        last_exc: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list) and len(data) >= 1:
                    return data
                return [data]
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(1 * (attempt + 1))
        raise RuntimeError(
            f"世行 API 请求失败 (已重试 {self.retries} 次): {last_exc}"
        ) from last_exc

    @staticmethod
    def _parse(raw: list[Any]) -> list[dict[str, Any]]:
        points: list[dict[str, Any]] = []
        try:
            if len(raw) < 2 or raw[1] is None:
                return points
            for item in raw[1]:
                val = item.get("value")
                try:
                    val = float(val) if val is not None else None
                except (ValueError, TypeError):
                    pass
                points.append({
                    "year": int(item.get("date", 0)),
                    "value": val,
                })
        except Exception:
            pass
        return points


def query(
    indicator: str, country: str = "CN", start: int = 2010, end: Optional[int] = None
) -> dict[str, Any]:
    """模块级快捷查询。"""
    return WBClient().query(indicator, country=country, start=start, end=end)


def cross_validate(
    nbs_indicator: str, nbs_value: float, year: int
) -> dict[str, Any]:
    """模块级快捷交叉验证。"""
    return WBClient().cross_validate(nbs_indicator, nbs_value, year)
