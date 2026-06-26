from .base import WarrantyProvider
from ..models import WarrantyQuery, WarrantyResult


STATUS_MAP = {
    "I": "IN WARRANTY",
    "O": "OUT OF WARRANTY",
    "E": "无效序列号",
}


class SeagateProvider(WarrantyProvider):
    brand_id = "seagate"
    display_name = "希捷"
    source_url = "https://www.seagate.com/cn/zh/support/warranty-and-replacements/"

    async def query(self, query: WarrantyQuery) -> WarrantyResult:
        return self._manual(
            query,
            "为什么机器人过不了人机验证，机器人不是人机吗？\n官网：https://www.seagate.com/cn/zh/support/warranty-and-replacements/",
        )

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
