import asyncio
import json
import re

import requests

from .base import WarrantyProvider
from ..models import WarrantyQuery, WarrantyResult


COUNTRY_VALUE = {
    "CN": "812",
    "US": "1",
    "CA": "806",
    "AU": "807",
    "GB": "808",
    "DE": "809",
    "JP": "810",
    "IN": "811",
    "HK": "922",
    "TW": "1031",
}

STATUS_MAP = {
    "IN LIMITED WARRANTY": "包含有限质保",
    "OUT OF LIMITED WARRANTY": "超出有限质保",
    "NO LIMITED WARRANTY": "不包含有限质保",
    "OUT OF REGION": "区域不符",
}


class WesternDigitalProvider(WarrantyProvider):
    brand_id = "western_digital"
    display_name = "西部数据"
    source_url = "https://support-cn.wd.com/app/warrantystatusweb"

    async def query(self, query: WarrantyQuery) -> WarrantyResult:
        return await asyncio.to_thread(self._query_sync, query)

    def _query_sync(self, query: WarrantyQuery) -> WarrantyResult:
        session = requests.Session()
        headers = {
            "user-agent": "Mozilla/5.0",
            "referer": self.source_url,
            "x-requested-with": "XMLHttpRequest",
        }
        try:
            page = session.get(self.source_url, headers=headers, timeout=20)
            page.raise_for_status()
            endpoint, w_id, rn_params = self._extract_widget(page.text)
            country = COUNTRY_VALUE.get(query.region.upper(), COUNTRY_VALUE["CN"])
            payload = {
                "w_id": w_id,
                "data": json.dumps({"country": country, "snlist": [query.serial.upper()]}, ensure_ascii=False),
                **rn_params,
            }
            resp = session.post(
                "https://support-cn.wd.com" + endpoint + self._session_suffix(page.url),
                headers=headers,
                data=payload,
                timeout=20,
            )
            resp.raise_for_status()
            body = resp.json() if resp.text.strip().startswith("{") else json.loads(resp.text)
            inner = body.get("response", body)
            if isinstance(inner, str):
                inner = json.loads(inner)
            rows = inner.get("slLookup") or []
            row = next((x for x in rows if str(x.get("sn", "")).upper() == query.serial.upper()), rows[0] if rows else {})
            if not row or not row.get("sn"):
                return self._manual(query, "官网接口没返回该序列号结果。")
            model = row.get("matlProdNum") or "未知"
            desc = row.get("description") or (row.get("productCatalog") or {}).get("Description") or ""
            status = row.get("warrantyStatus") or self._status(row)
            expire = row.get("wexpDate") or row.get("warrantyExpDate") or row.get("expDate") or "未知"
            if expire in ("", "-", None):
                expire = "未知"
            return WarrantyResult(
                ok=True,
                brand=self.display_name,
                serial=query.serial.upper(),
                model=model,
                status=status,
                expire_date=expire,
                region=query.region,
                message=desc,
                source_url=self.source_url,
                raw=row,
                need_manual=False,
            )
        except Exception as e:
            return self._manual(query, f"WD官网接口请求失败：{e}")

    def _extract_widget(self, html: str) -> tuple[str, str, dict[str, str]]:
        pattern = re.compile(r'W\[c\]\((\{"i":\{"c":"warrantyStatusweb".*?\}),\s*\'([^\']*)\',\s*\'([^\']*)\',\s*(\d+),\s*\'([^\']+)\',\s*\'custom/Warranty/warrantyStatusweb\',\s*\'Custom\.Widgets\.Warranty\.warrantyStatusweb\',\s*\'\d+\',\s*\'([^\']+)\'', re.S)
        match = pattern.search(html)
        if not match:
            raise RuntimeError("找不到WD质保组件")
        cfg = json.loads(match.group(1))
        endpoint = cfg.get("a", {}).get("default_ajax_endpoint")
        rn_context_data, rn_context_token, rn_timestamp, w_id, rn_form_token = match.group(2), match.group(3), match.group(4), match.group(5), match.group(6)
        if not endpoint or not w_id:
            raise RuntimeError("WD组件参数不完整")
        return endpoint, w_id, {
            "rn_contextData": rn_context_data,
            "rn_contextToken": rn_context_token,
            "rn_timestamp": rn_timestamp,
            "rn_formToken": rn_form_token,
        }

    def _session_suffix(self, page_url: str) -> str:
        match = re.search(r"(/session/[^/?#]+)", page_url)
        return match.group(1) if match else ""

    def _status(self, row: dict) -> str:
        code = str(row.get("warrantyCode") or "").upper()
        text = str(row.get("warrantyStatus") or row.get("status") or "").upper()
        hay = code + " " + text
        for key, val in STATUS_MAP.items():
            if key in hay:
                return val
        if code.startswith("IN"):
            return "包含有限质保"
        if code.startswith("OUT"):
            return "超出有限质保"
        if code.startswith("NO"):
            return "不包含有限质保"
        return row.get("warrantyStatus") or row.get("status") or "未知"

    def _manual(self, query: WarrantyQuery, message: str) -> WarrantyResult:
        return WarrantyResult(
            ok=False,
            brand=self.display_name,
            serial=query.serial.upper(),
            region=query.region,
            status="需要人工确认",
            message=message,
            source_url=self.source_url,
            need_manual=True,
        )
