from abc import ABC, abstractmethod

from ..models import WarrantyQuery, WarrantyResult


class WarrantyProvider(ABC):
    brand_id = "base"
    display_name = "Base"
    source_url = ""

    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    @abstractmethod
    async def query(self, query: WarrantyQuery) -> WarrantyResult:
        raise NotImplementedError
