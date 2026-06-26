import asyncio
import urllib.parse
from typing import Any

import requests

from ..models import NandBlock, NandField, NandQuery, NandResult

FDNEXT_API_BASE = "https://fdnext.itxtech.org"


class NandProvider:
    """NAND 物料/颗粒查询 Provider，基于 iTXTech fdnext 引擎"""

    source_url = "https://fm.itxtech.org"

    def __init__(self, timeout: int = 15, api_base: str = ""):
        self.timeout = timeout
        self.api_base = (api_base or "").rstrip("/") or FDNEXT_API_BASE

    async def query(self, nand_query: NandQuery) -> NandResult:
        return await asyncio.to_thread(self._query_sync, nand_query)

    def _query_sync(self, nand_query: NandQuery) -> NandResult:
        if nand_query.query_type == "flash_id":
            endpoint = "/identifiers/decode"
            params = {
                "query": nand_query.query,
                "lang": "chs",
                "idScheme": "nand.flash_id",
            }
        else:
            endpoint = "/parts/decode"
            params = {
                "query": nand_query.query,
                "lang": "chs",
            }

        url = f"{self.api_base}{endpoint}?{urllib.parse.urlencode(params)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }

        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            return self._parse_response(nand_query, data)
        except requests.exceptions.HTTPError as e:
            # fdnext 返回 400 表示 invalid_input / unsupported
            if e.response is not None and e.response.status_code == 400:
                try:
                    err_data = e.response.json()
                    err_msg = err_data.get("status", str(e))
                except Exception:
                    err_msg = str(e)
                return NandResult(
                    ok=False,
                    query=nand_query.query,
                    query_type=nand_query.query_type,
                    error=f"查询失败: {err_msg}",
                    raw={},
                )
            return NandResult(
                ok=False,
                query=nand_query.query,
                query_type=nand_query.query_type,
                error=f"fdnext 接口请求失败: {e}",
                raw={},
            )
        except Exception as e:
            return NandResult(
                ok=False,
                query=nand_query.query,
                query_type=nand_query.query_type,
                error=f"请求失败: {e}",
                raw={},
            )

    def _parse_response(self, nand_query: NandQuery, data: dict[str, Any]) -> NandResult:
        status = data.get("status", "")
        if status not in ("ok", ""):
            return NandResult(
                ok=False,
                query=nand_query.query,
                query_type=nand_query.query_type,
                error=f"fdnext 返回状态: {status}",
                raw=data,
            )

        device = data.get("device", {})
        vendor = device.get("vendor", {})
        part_number = device.get("partNumber", nand_query.query)

        blocks: list[NandBlock] = []
        for block_data in data.get("blocks", []):
            fields: list[NandField] = []
            for f in block_data.get("fields", []):
                value = f.get("value")
                display = f.get("display", "")
                if not display and value is not None:
                    display = str(value)
                # 列表型字段（如控制器列表）取前几个展示
                if isinstance(value, list):
                    display = ", ".join(str(v) for v in value[:8])
                    if len(value) > 8:
                        display += f" ... 等共{len(value)}个"
                fields.append(NandField(
                    key=f.get("key", ""),
                    label=f.get("label", ""),
                    value=value,
                    display=display,
                ))
            blocks.append(NandBlock(
                id=block_data.get("id", ""),
                label=block_data.get("label", ""),
                fields=fields,
            ))

        return NandResult(
            ok=True,
            query=nand_query.query,
            query_type=nand_query.query_type,
            subtitle=data.get("subtitle", ""),
            vendor=vendor.get("name", ""),
            part_number=part_number,
            chip_kind=device.get("chipKind", ""),
            blocks=blocks,
            source_url=self.source_url,
            raw=data,
        )

    @staticmethod
    def format_result(result: NandResult) -> str:
        """格式化 NAND 查询结果为文本消息"""
        if not result.ok:
            return f"NAND 物料查询失败: {result.error}"

        lines = ["NAND 物料查询"]
        if result.subtitle:
            lines.append(result.subtitle)
        lines.append(f"型号: {result.part_number}")
        if result.vendor:
            lines.append(f"厂商: {result.vendor}")

        for block in result.blocks:
            # 跳过控制器列表块（太长）
            if block.id == "controllers":
                ctrl_count = sum(
                    len(f.value) if isinstance(f.value, list) else 1
                    for f in block.fields
                    if f.key == "controller"
                )
                if ctrl_count > 0:
                    lines.append(f"支持主控: {ctrl_count} 种（详见 FlashMaster）")
                continue

            for f in block.fields:
                if f.display and f.key != "controller":
                    label = f.label or f.key
                    lines.append(f"{label}: {f.display}")

        lines.append(f"来源: {result.source_url}")
        return "\n".join(lines)