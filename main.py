import asyncio
import importlib.util
import re
from pathlib import Path
from typing import Optional

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .cache import JsonCache
from .models import NandQuery, WarrantyQuery, WarrantyResult
from .providers import build_nand_provider, build_providers


INTENT_WORDS = ("保修", "质保", "warranty", "rma")
NAND_INTENT_WORDS = ("颗粒", "物料", "flash id", "flashid", "nand", "pn查询", "型号查询")
REGION_ALIASES = {
    "中国": "CN",
    "大陆": "CN",
    "国内": "CN",
    "cn": "CN",
    "香港": "HK",
    "hk": "HK",
    "台湾": "TW",
    "tw": "TW",
    "美国": "US",
    "us": "US",
}
REGION_DISPLAY = {
    "CN": "中国",
    "HK": "中国香港",
    "TW": "中国台湾",
    "US": "美国",
}


@register(
    "在线硬件查询",
    "BA7LCY",
    "在线硬件产品查询工具，支持质保查询、NAND颗粒物料识别等功能，支持文本和图片识别",
    "1.3.0",
)
class WarrantyCheckerPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self.enabled = bool(self.config.get("enabled", True))
        self.default_region = str(self.config.get("default_region", "CN")).upper()
        self.cache_ttl_seconds = int(self.config.get("cache_ttl_seconds", 7 * 24 * 3600))
        self.timeout_seconds = int(self.config.get("timeout_seconds", 15))
        self.llm_summary = bool(self.config.get("llm_summary", False))
        self.image_sn_mode = str(self.config.get("image_sn_mode", "llm")).lower().strip()
        if self.image_sn_mode == "本地ocr":
            self.image_sn_mode = "local_ocr"
        if self.image_sn_mode not in {"llm", "local_ocr", "off"}:
            self.image_sn_mode = "llm"
        self.min_sn_len = int(self.config.get("min_sn_len", 8))
        self.max_sn_len = int(self.config.get("max_sn_len", 32))
        self.brand_aliases = self.config.get("brand_aliases", {}) or {}
        self.trigger_keywords = self.config.get("trigger_keywords", ["质保"])
        if isinstance(self.trigger_keywords, str):
            self.trigger_keywords = [kw.strip() for kw in self.trigger_keywords.split(",") if kw.strip()]
        if not self.brand_aliases:
            self.brand_aliases = {
                "western_digital": ["西数", "西部数据", "wd", "western digital"],
                "seagate": ["希捷", "seagate", "海门"],
                "toshiba": ["东芝", "toshiba"],
                "sandisk": ["闪迪", "sandisk"],
                "ymtc": ["致态", "致钛", "ymtc", "长江存储"],
            }

        base_dir = Path(__file__).resolve().parent
        self.cache = JsonCache(base_dir / "data" / "cache.json", self.cache_ttl_seconds)
        self.providers = build_providers(self.timeout_seconds, self.image_sn_mode)
        nand_api_base = str(self.config.get("nand_api_base", "")).strip()
        self.nand_provider = build_nand_provider(self.timeout_seconds, nand_api_base)
        self.nand_enabled = bool(self.config.get("nand_query_enabled", True))
        nand_kw_cfg = self.config.get("nand_trigger_keywords", list(NAND_INTENT_WORDS))
        if isinstance(nand_kw_cfg, str):
            nand_kw_cfg = [kw.strip() for kw in nand_kw_cfg.split(",") if kw.strip()]
        self.nand_trigger_keywords = [str(kw).strip().lower() for kw in nand_kw_cfg if str(kw).strip()]
        if not self.nand_trigger_keywords:
            self.nand_trigger_keywords = list(NAND_INTENT_WORDS)
        self.alias_to_brand = self._build_alias_map()

    def _build_alias_map(self) -> dict[str, str]:
        alias_map = {}
        for brand_id, aliases in self.brand_aliases.items():
            for alias in aliases:
                alias = str(alias).strip().lower()
                if alias:
                    alias_map[alias] = str(brand_id)
        return alias_map

    def _has_intent(self, text: str) -> bool:
        lower = text.lower()
        return any(word in lower for word in INTENT_WORDS)

    def _has_nand_intent(self, text: str) -> bool:
        lower = text.lower()
        return any(word in lower for word in self.nand_trigger_keywords)

    def _find_brand(self, text: str) -> Optional[tuple[str, str]]:
        lower = text.lower()
        hits = []
        for alias, brand_id in self.alias_to_brand.items():
            if alias and alias in lower:
                hits.append((len(alias), alias, brand_id))
        if not hits:
            return None
        _, alias, brand_id = sorted(hits, reverse=True)[0]
        return brand_id, alias

    def _find_region(self, text: str) -> str:
        lower = text.lower()
        for alias, region in REGION_ALIASES.items():
            if alias.lower() in lower:
                return region
        return self.default_region

    def _clean_text(self, text: str) -> str:
        text = re.sub(r"\[CQ:[^\]]+\]", " ", text)
        text = re.sub(r"^\s*@\S+\s*", "", text)
        return text.strip()

    def _find_serial(self, text: str, brand_alias: str) -> Optional[str]:
        tmp = text
        for word in list(INTENT_WORDS) + [brand_alias]:
            tmp = re.sub(re.escape(word), " ", tmp, flags=re.I)
        for alias in REGION_ALIASES.keys():
            tmp = re.sub(re.escape(alias), " ", tmp, flags=re.I)
        tmp = re.sub(r"(查一下|帮我|查询|查|一下|的|码|序列号|sn|SN|：|:)", " ", tmp)
        candidates = re.findall(r"(?<![A-Za-z0-9])[A-Za-z0-9][A-Za-z0-9\-]{%d,%d}(?![A-Za-z0-9])" % (self.min_sn_len - 1, self.max_sn_len - 1), tmp)
        candidates = [c.strip("-_").upper() for c in candidates if c.strip("-_")]
        if not candidates:
            return None
        return max(candidates, key=len)

    def _parse_nand_query(self, text: str) -> Optional[NandQuery]:
        """解析 NAND 物料查询请求。支持：
        - 颗粒 MT29F64G08CBABA
        - 物料查询 2C64444BA900
        - flash id 2C64444BA900
        - pn查询 MT29F1T08EQLCEB2
        """
        if not self.nand_enabled:
            return None
        lower = text.lower()
        nand_kw = None
        for kw in self.nand_trigger_keywords:
            if kw in lower:
                nand_kw = kw
                break
        if not nand_kw:
            return None
        # 去掉关键词和常见前缀，提取查询文本
        tmp = text
        for kw in self.nand_trigger_keywords:
            tmp = re.sub(re.escape(kw), " ", tmp, flags=re.I)
        tmp = re.sub(r"(查一下|帮我|查询|查|一下|的|：|:)", " ", tmp)
        tmp = re.sub(r"\[CQ:[^\]]+\]", " ", tmp)
        tmp = re.sub(r"^\s*@\S+\s*", "", tmp)
        tmp = tmp.strip()
        if not tmp:
            return None
        # 去掉可能的空格分隔（flash id 可能是 "2C,64,44,4B,A9,00" 或 "2C 64 44 4B A9 00"）
        cleaned = re.sub(r"[\s,]+", "", tmp)
        if not cleaned:
            return None
        # Flash ID 通常是纯 hex，12-16 位
        # Part Number 通常包含字母和数字混合，如 MT29F...
        if re.fullmatch(r"[0-9A-Fa-f]{4,16}", cleaned):
            return NandQuery(query=cleaned.upper(), query_type="flash_id")
        # 默认当 PN 处理
        return NandQuery(query=cleaned.upper(), query_type="part_number")

    def _parse_query(self, text: str) -> Optional[WarrantyQuery]:
        text = self._clean_text(text)
        kw_pattern = "|".join(re.escape(kw) for kw in self.trigger_keywords)
        match = re.match(r"^\s*(?:%s)\s+(\S+)\s+([A-Za-z0-9][A-Za-z0-9\-_]{%d,%d})\s*$" % (kw_pattern, self.min_sn_len - 1, self.max_sn_len - 1), text, re.I)
        if not match:
            return None
        brand_text, serial = match.groups()
        brand_hit = self._find_brand(brand_text)
        if not brand_hit:
            return None
        brand_id, _ = brand_hit
        if brand_id not in self.providers:
            return None
        provider = self.providers[brand_id]
        return WarrantyQuery(
            brand=brand_id,
            brand_name=provider.display_name,
            serial=serial.strip("-_").upper(),
            region=self.default_region,
        )

    def _cache_key(self, query: WarrantyQuery) -> str:
        return f"{query.brand}:{query.region}:{query.serial}".lower()

    def _format_result(self, result: WarrantyResult, from_cache: bool = False) -> str:
        if result.brand == "希捷" and result.need_manual:
            return result.message or "希捷质保查询失败"
        prefix = "质保查询"
        if from_cache:
            prefix += "(缓存)"
        lines = [
            f"{prefix}：{result.brand}",
            f"序列号：{result.serial}",
            f"质保状态：{result.status}",
            f"型号：{result.model}",
            f"描述：{result.message or '未知'}",
        ]
        if result.expire_date and result.expire_date != "未知":
            lines.append(f"到期日期：{result.expire_date}")
        if result.region and result.region != "未知":
            lines.append(f"地区：{REGION_DISPLAY.get(result.region, result.region)}")
        if result.source_url:
            lines.append(result.source_url)
        return "\n".join(lines)

    async def _maybe_summarize(self, result: WarrantyResult) -> str:
        # 预留位置：默认不开，避免每次耗token。不同AstrBot版本LLM接口不完全一致，先保守返回模板。
        return self._format_result(result)

    def _extract_images(self, event: AstrMessageEvent) -> list[str]:
        images: list[str] = []
        try:
            for comp in getattr(event.message_obj, "message", []) or []:
                url = getattr(comp, "url", None)
                file = getattr(comp, "file", None)
                path = getattr(comp, "path", None)
                if url:
                    images.append(str(url))
                elif path:
                    images.append(str(path))
                elif file:
                    images.append(str(file))
        except Exception:
            pass
        return images

    def _extract_query_from_ocr_text(self, text: str) -> Optional[WarrantyQuery]:
        brand_hit = self._find_brand(text)
        if not brand_hit:
            return None
        brand_id, brand_alias = brand_hit
        if brand_id not in self.providers:
            return None
        serial = self._find_serial(text, brand_alias)
        if not serial:
            m = re.search(r"S\s*/?\s*N\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9\-_]{%d,%d})" % (self.min_sn_len - 1, self.max_sn_len - 1), text, re.I)
            if m:
                serial = m.group(1).strip("-_").upper()
        if not serial:
            return None
        provider = self.providers[brand_id]
        return WarrantyQuery(brand=brand_id, brand_name=provider.display_name, serial=serial, region=self.default_region)

    async def _query_from_images(self, event: AstrMessageEvent, text: str) -> Optional[WarrantyQuery]:
        if self.image_sn_mode == "off":
            return None
        images = self._extract_images(event)
        if not images:
            return None
        if self.image_sn_mode == "local_ocr":
            return await self._query_from_images_local_ocr(images)
        return await self._query_from_images_llm(event, images, text)

    async def _query_from_images_local_ocr(self, images: list[str]) -> Optional[WarrantyQuery]:
        if importlib.util.find_spec("easyocr") is None:
            raise RuntimeError("本地OCR未安装，请在AstrBot虚拟环境执行：python -m pip install easyocr")

        def run_ocr() -> str:
            import easyocr  # type: ignore
            reader = easyocr.Reader(["en"], gpu=False)
            parts: list[str] = []
            for img in images[:1]:
                parts.extend(str(x) for x in reader.readtext(img, detail=0, paragraph=False))
            return "\n".join(parts)

        ocr_text = await asyncio.to_thread(run_ocr)
        return self._extract_query_from_ocr_text(ocr_text)

    async def _query_from_images_llm(self, event: AstrMessageEvent, images: list[str], text: str) -> Optional[WarrantyQuery]:
        provider = self.context.get_using_provider(event.unified_msg_origin)
        if not provider:
            raise RuntimeError("未配置可用LLM Provider")
        prompt = (
            "从图片里的产品标签提取品牌和序列号SN。"
            "只返回JSON，不要解释，格式："
            "{\"brand\":\"wd或seagate或unknown\",\"serial\":\"SN\"}。"
            "如果识别不到serial就返回空字符串。\n"
            f"用户文本：{text}"
        )
        resp = await provider.text_chat(
            prompt=prompt,
            session_id=getattr(event, "session_id", None),
            image_urls=images[:1],
            persist=False,
        )
        raw = getattr(resp, "completion_text", "") or str(resp)
        brand = None
        m_brand = re.search(r'"brand"\s*:\s*"([^"\n]+)"', raw, re.I)
        if m_brand:
            brand = m_brand.group(1).strip().lower()
        m_sn = re.search(r'"serial"\s*:\s*"([A-Za-z0-9\-_]{%d,%d})"' % (self.min_sn_len, self.max_sn_len), raw, re.I)
        serial = m_sn.group(1).strip("-_").upper() if m_sn else None
        if not serial:
            return None
        if brand not in self.providers:
            return None
        provider_obj = self.providers.get(brand)
        if not provider_obj:
            return None
        return WarrantyQuery(brand=brand, brand_name=provider_obj.display_name, serial=serial, region=self._find_region(text))

    async def _query_nand_from_images(self, event: AstrMessageEvent, text: str) -> Optional[NandQuery]:
        """从图片中识别 NAND 颗粒型号（PN 或 Flash ID）"""
        if self.image_sn_mode == "off":
            return None
        images = self._extract_images(event)
        if not images:
            return None

        if self.image_sn_mode == "local_ocr":
            return await self._query_nand_from_images_local_ocr(images)

        # LLM 模式：让 LLM 从芯片照片中提取 PN
        provider = self.context.get_using_provider(event.unified_msg_origin)
        if not provider:
            raise RuntimeError("未配置可用LLM Provider")
        prompt = (
            "从图片里的 NAND Flash 芯片上提取丝印型号（Part Number）。"
            "芯片上通常印有厂商 logo 和类似 MT29F、K9ABGD、H27Q 等开头的型号字符串。\n"
            "只返回JSON，不要解释，格式："
            "{\"pn\":\"型号字符串\",\"type\":\"pn或flash_id\"}。\n"
            "type 为 pn 表示 Part Number，flash_id 表示 Flash ID（纯十六进制）。\n"
            "如果识别不到就返回 {\"pn\":\"\",\"type\":\"\"}。\n"
            f"用户文本：{text}"
        )
        resp = await provider.text_chat(
            prompt=prompt,
            session_id=getattr(event, "session_id", None),
            image_urls=images[:1],
            persist=False,
        )
        raw = getattr(resp, "completion_text", "") or str(resp)
        m_pn = re.search(r'"pn"\s*:\s*"([^"]+)"', raw, re.I)
        pn = m_pn.group(1).strip() if m_pn else ""
        if not pn:
            return None
        m_type = re.search(r'"type"\s*:\s*"([^"]+)"', raw, re.I)
        qtype = m_type.group(1).strip().lower() if m_type else ""
        if qtype == "flash_id":
            cleaned = re.sub(r"[\s,]+", "", pn).upper()
            return NandQuery(query=cleaned, query_type="flash_id")
        return NandQuery(query=pn.upper(), query_type="part_number")

    async def _query_nand_from_images_local_ocr(self, images: list[str]) -> Optional[NandQuery]:
        """从图片中用本地 OCR 识别 NAND 颗粒型号"""
        if importlib.util.find_spec("easyocr") is None:
            raise RuntimeError("本地OCR未安装，请在AstrBot虚拟环境执行：python -m pip install easyocr")

        def run_ocr() -> str:
            import easyocr  # type: ignore
            reader = easyocr.Reader(["en"], gpu=False)
            parts: list[str] = []
            for img in images[:1]:
                parts.extend(str(x) for x in reader.readtext(img, detail=0, paragraph=False))
            return "\n".join(parts)

        ocr_text = await asyncio.to_thread(run_ocr)
        # 从 OCR 文本中尝试匹配 PN 模式
        # 常见 NAND PN 前缀
        pn_patterns = [
            r"((?:MT|K9|H27|TC58|TH58|SDTNRGAMA|SDINBD|WD|SanDisk)[A-Za-z0-9]{6,})",
            r"([A-Z]{2,3}[0-9][A-Z][A-Za-z0-9]{8,})",
        ]
        for pat in pn_patterns:
            m = re.search(pat, ocr_text, re.I)
            if m:
                return NandQuery(query=m.group(1).upper(), query_type="part_number")
        # 尝试匹配 Flash ID（纯 hex，12-16 位）
        m_hex = re.search(r"\b([0-9A-Fa-f]{12,16})\b", ocr_text)
        if m_hex:
            return NandQuery(query=m_hex.group(1).upper(), query_type="flash_id")
        return None

    @filter.event_message_type(filter.EventMessageType.ALL, priority=20)
    async def on_message(self, event: AstrMessageEvent):
        if not self.enabled:
            return
        if not bool(getattr(event, "is_at_or_wake_command", False)):
            return
        try:
            text = event.message_str or event.get_message_str() or ""
        except Exception:
            text = ""
        # 优先检查 NAND 物料查询意图
        nand_query = self._parse_nand_query(text)
        if not nand_query and self._has_nand_intent(text):
            # 有 NAND 意图但没有解析出查询文本，尝试从图片识别
            try:
                nand_query = await self._query_nand_from_images(event, text)
            except RuntimeError as e:
                yield event.plain_result(str(e))
                event.should_call_llm(False)
                return
            except Exception as e:
                logger.warning(f"[online_hardware] nand image extract failed: {e}")
        if nand_query:
            try:
                nand_result = await self.nand_provider.query(nand_query)
                from .providers.nand import NandProvider
                reply = NandProvider.format_result(nand_result)
            except Exception as e:
                logger.warning(f"[online_hardware] nand query failed: {e}")
                reply = f"NAND 物料查询失败: {e}"
            yield event.plain_result(reply)
            event.should_call_llm(False)
            return

        # 质保查询流程
        query = self._parse_query(text)
        if not query:
            has_brand_in_text = self._find_brand(text) is not None
            has_intent_in_text = self._has_intent(text)
            if has_brand_in_text or has_intent_in_text:
                try:
                    query = await self._query_from_images(event, text)
                except RuntimeError as e:
                    yield event.plain_result(str(e))
                    event.should_call_llm(False)
                    return
                except Exception as e:
                    logger.warning(f"[online_hardware] image sn extract failed: {e}")
                    yield event.plain_result("图片SN识别失败")
                    event.should_call_llm(False)
                    return
        if not query:
            return

        key = self._cache_key(query)
        cached = self.cache.get(key)
        if cached:
            yield event.plain_result(self._format_result(cached, from_cache=True))
            event.should_call_llm(False)
            return

        provider = self.providers.get(query.brand)
        if not provider:
            return

        try:
            result = await provider.query(query)
        except Exception as e:
            logger.warning(f"[online_hardware] query failed brand={query.brand} sn={query.serial}: {e}")
            result = WarrantyResult(
                ok=False,
                brand=query.brand_name,
                serial=query.serial,
                region=query.region,
                status="查询失败",
                message="查询失败，可能是官网接口变了或需要验证码。",
                source_url=getattr(provider, "source_url", ""),
                need_manual=True,
            )

        self.cache.set(key, result)
        if self.llm_summary:
            reply = await self._maybe_summarize(result)
        else:
            reply = self._format_result(result)
        yield event.plain_result(reply)
        event.should_call_llm(False)
