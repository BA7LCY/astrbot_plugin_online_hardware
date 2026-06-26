from dataclasses import dataclass, field
from typing import Any


@dataclass
class WarrantyQuery:
    brand: str
    brand_name: str
    serial: str
    region: str = "CN"


@dataclass
class WarrantyResult:
    ok: bool
    brand: str
    serial: str
    model: str = "未知"
    status: str = "未知"
    expire_date: str = "未知"
    region: str = "CN"
    message: str = ""
    source_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    need_manual: bool = False


@dataclass
class NandQuery:
    """NAND 物料/颗粒查询请求"""
    query: str  # 原始查询文本（PN 或 Flash ID）
    query_type: str = "part_number"  # "part_number" | "flash_id"


@dataclass
class NandField:
    """单个字段"""
    key: str
    label: str
    value: Any = None
    display: str = ""


@dataclass
class NandBlock:
    """一组字段（如"存储"、"几何信息"）"""
    id: str
    label: str
    fields: list[NandField] = field(default_factory=list)


@dataclass
class NandResult:
    """NAND 物料/颗粒查询结果"""
    ok: bool
    query: str
    query_type: str = "part_number"
    subtitle: str = ""
    vendor: str = ""
    part_number: str = ""
    chip_kind: str = ""
    blocks: list[NandBlock] = field(default_factory=list)
    source_url: str = "https://fdnext.itxtech.org"
    raw: dict[str, Any] = field(default_factory=dict)
    error: str = ""