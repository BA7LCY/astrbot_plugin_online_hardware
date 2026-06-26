import asyncio
import json
import re

import requests

from .base import WarrantyProvider
from ..models import WarrantyQuery, WarrantyResult


class SandiskProvider(WarrantyProvider):
    brand_id = "sandisk"
    display_name = "闪迪"
    source_url = "https://support-cn.sandisk.com/app/warrantystatusweb"

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
            payload = {
                "w_id": w_id,
                "data": json.dumps({"country": "812", "snlist": [query.serial.upper()]}, ensure_ascii=False),
                **rn_params,
            }
            resp = session.post(
                "https://support-cn.sandisk.com" + endpoint,
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
                return self._manual(query, "闪迪官网接口没返回该序列号结果。")
            model = row.get("mn") or "未知"
            desc = row.get("description") or ""
            status = row.get("warrantyStatus") or "未知"
            # 用sdWarranty.WarrantyTerm作为到期日期，没有则用wexpDate
            warranty_term = (row.get("sdWarranty") or {}).get("WarrantyTerm") or ""
            expire = row.get("wexpDate") or "未知"
            if warranty_term:
                expire = f"{warranty_term} [自购买日起]"
            elif expire in ("", "-", None):
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
            return self._manual(query, f"闪迪官网接口请求失败：{e}")

    def _extract_widget(self, html: str) -> tuple[str, str, dict[str, str]]:
        pattern = re.compile(r'W\[c\]\((\{"i":\{"c":"warrantyStatusweb".*?\}),\s*\'([^\']*)\',\s*\'([^\']*)\',\s*(\d+),\s*\'([^\']+)\',\s*\'custom/Warranty/warrantyStatusweb\',\s*\'Custom\.Widgets\.Warranty\.warrantyStatusweb\',\s*\'\d+\',\s*\'([^\']+)\'', re.S)
        match = pattern.search(html)
        if not match:
            raise RuntimeError("找不到闪迪质保组件")
        cfg = json.loads(match.group(1))
        endpoint = cfg.get("a", {}).get("default_ajax_endpoint")
        rn_context_data, rn_context_token, rn_timestamp, w_id, rn_form_token = match.group(2), match.group(3), match.group(4), match.group(5), match.group(6)
        if not endpoint or not w_id:
            raise RuntimeError("闪迪组件参数不完整")
        return endpoint, w_id, {
            "rn_contextData": rn_context_data,
            "rn_contextToken": rn_context_token,
            "rn_timestamp": rn_timestamp,
            "rn_formToken": rn_form_token,
        }

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
