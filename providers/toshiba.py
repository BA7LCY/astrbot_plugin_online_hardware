import asyncio
import re

import requests

from .base import WarrantyProvider
from ..models import WarrantyQuery, WarrantyResult


STATUS_MAP = {
    "IN": "保修内",
    "OUT": "过保",
    "Nodata": "查无资料",
    "Duplicate": "资料重复输入",
    "Limited Warranty": "有限保固",
}


class ToshibaProvider(WarrantyProvider):
    brand_id = "toshiba"
    display_name = "东芝"
    source_url = "https://telsx.cn/Toshiba_Warranty/WarrantyCheckCN.do"

    async def query(self, query: WarrantyQuery) -> WarrantyResult:
        return await asyncio.to_thread(self._query_sync, query)

    def _query_sync(self, query: WarrantyQuery) -> WarrantyResult:
        session = requests.Session()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Origin": "https://telsx.cn",
            "Referer": self.source_url,
        }
        try:
            # multipart/form-data POST
            form_data = {
                "command": (None, "query"),
                "txtSerialNum": (None, query.serial.upper()),
                "filename": ("", "", "application/octet-stream"),
            }

            resp = session.post(self.source_url, files=form_data, headers=headers, timeout=20)
            resp.raise_for_status()
            html = resp.text

            # 解析结果表格
            rows = re.findall(r'<tr[^>]*>\s*(.*?)\s*</tr>', html, re.DOTALL)
            
            # 存储汇总信息和详细结果
            summary_data = []
            detail_data = None
            
            for row in rows:
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                if not cells:
                    continue
                    
                cleaned = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
                
                # 汇总表格行（3列）：[状态代码, 状态描述, 笔数]
                if len(cleaned) == 3 and cleaned[0] in STATUS_MAP:
                    status_code = cleaned[0]
                    status_desc = cleaned[1]
                    count = cleaned[2]
                    summary_data.append((status_code, status_desc, count))
                
                # 详细结果行（4列以上）：[状态, SN, 型号, 产品料号, 移动硬盘型号]
                elif len(cleaned) >= 4 and cleaned[1].upper() == query.serial.upper():
                    status_raw = cleaned[0]
                    status = STATUS_MAP.get(status_raw, status_raw)
                    model = cleaned[2] if len(cleaned) > 2 else "未知"
                    part_num = cleaned[3] if len(cleaned) > 3 else "未知"
                    desc = cleaned[4] if len(cleaned) > 4 else ""
                    detail_data = (status_raw, status, model, part_num, desc)

            # 构建汇总信息
            summary_lines = []
            for status_code, status_desc, count in summary_data:
                summary_lines.append(f"{status_code}\t{status_desc}\t{count}")
            summary_text = "\n".join(summary_lines)

            # 构建详细结果
            if detail_data:
                status_raw, status, model, part_num, desc = detail_data
                detail_text = f"保修状态: {status}\n序号: {query.serial.upper()}\n机型: {model}\n产品料号: {part_num}"
                if desc:
                    detail_text += f"\n移动硬盘型号: {desc}"

                # 组合汇总和详细结果
                full_message = f"查询结果:\n{summary_text}\n\n详细信息:\n{detail_text}"

                return WarrantyResult(
                    ok=True,
                    brand=self.display_name,
                    serial=query.serial.upper(),
                    model=model,
                    status=status,
                    region=query.region,
                    message=full_message,
                    source_url=self.source_url,
                    raw={
                        "summary": summary_data,
                        "status": status_raw,
                        "model": model,
                        "part_number": part_num,
                        "description": desc
                    },
                    need_manual=False,
                )

            # 没找到匹配的SN，检查是否Nodata
            return WarrantyResult(
                ok=False,
                brand=self.display_name,
                serial=query.serial.upper(),
                region=query.region,
                status="查无资料",
                message=f"查询结果:\n{summary_text}\n\n东芝官网接口未返回该序列号详细结果",
                source_url=self.source_url,
                need_manual=True,
            )

        except Exception as e:
            return self._manual(query, f"东芝官网接口请求失败：{e}")

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
