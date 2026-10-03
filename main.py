import re
from pathlib import Path
from typing import Optional

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .cache import JsonCache
from .hardware_inspection import HardwareInspector, InspectionError
from .models import NandQuery, WarrantyQuery, WarrantyResult
from .providers import build_nand_provider, build_providers
from .vision_client import VisionClient, normalize_provider_ids


INTENT_WORDS = ("保修", "质保", "warranty", "rma")
NAND_INTENT_WORDS = ("查颗粒", "查物料", "查flash id", "查flashid", "查nand", "查pn", "查型号")
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


T2I_ACCENTS = {
    "ok": "#42bd8f",
    "warn": "#e8a33d",
    "bad": "#e35d6a",
    "muted": "#8898aa",
}
VERDICT_ACCENTS = {
    "未发现明显矛盾": T2I_ACCENTS["ok"],
    "发现可疑矛盾": T2I_ACCENTS["bad"],
    "证据不足": T2I_ACCENTS["warn"],
}


@register(
    "在线硬件查询",
    "BA7LCY",
    "在线硬件产品查询工具，支持质保查询、NAND颗粒物料识别等功能，支持文本和图片识别",
    "1.5.0",
)
class WarrantyCheckerPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self.hardware_inspection_enabled = bool(self.config.get("hardware_inspection_enabled", False))
        self.hardware_inspector = HardwareInspector(
            str(self.config.get("hardware_api_base", "") or ""),
            str(self.config.get("hardware_api_key", "") or ""),
            str(self.config.get("hardware_model", "") or ""),
            int(self.config.get("hardware_timeout_seconds", 180)),
        )
        self.default_region = str(self.config.get("default_region", "CN")).upper()
        self.cache_ttl_seconds = int(self.config.get("cache_ttl_seconds", 7 * 24 * 3600))
        self.timeout_seconds = int(self.config.get("timeout_seconds", 15))
        self.llm_summary = bool(self.config.get("llm_summary", False))
        self.t2i_enabled = bool(self.config.get("t2i_enabled", False))
        self.image_recognition_enabled = bool(self.config.get("image_recognition_enabled", True))
        self.vision_provider_id = str(self.config.get("vision_provider_id", "") or "").strip()
        self.vision_fallback_provider_ids = normalize_provider_ids(
            self.config.get("vision_fallback_provider_ids", [])
        )
        self.vision_timeout_seconds = max(
            0,
            int(self.config.get("vision_timeout_seconds", 60)),
        )
        self.image_prompt = str(self.config.get("image_prompt", "") or "").strip()
        self.nand_image_prompt = str(self.config.get("nand_image_prompt", "") or "").strip()
        self.fail_message = str(self.config.get("fail_message", "看不清图，别用锁泥相机拍") or "")
        self.min_sn_len = int(self.config.get("min_sn_len", 8))
        self.max_sn_len = int(self.config.get("max_sn_len", 32))
        self.brand_aliases = self.config.get("brand_aliases", {}) or {}
        self.trigger_keywords = self.config.get("trigger_keywords", ["查质保", "查保修"])
        if isinstance(self.trigger_keywords, str):
            self.trigger_keywords = [kw.strip() for kw in self.trigger_keywords.split(",") if kw.strip()]
        self.trigger_keywords = self._normalize_command_keywords(self.trigger_keywords, {"质保": "查质保", "保修": "查保修"})
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
        self._report_template = ""
        try:
            self._report_template = (base_dir / "templates" / "hardware_report.html").read_text(encoding="utf-8")
        except OSError:
            logger.warning("[online_hardware] t2i template missing, card output disabled")
        self.vision_client = VisionClient(
            self.context,
            [self.vision_provider_id, *self.vision_fallback_provider_ids],
            timeout_seconds=self.vision_timeout_seconds,
            logger=logger,
        )
        self.providers = build_providers(self.timeout_seconds)
        nand_api_base = str(self.config.get("nand_api_base", "")).strip()
        self.nand_provider = build_nand_provider(self.timeout_seconds, nand_api_base)
        self.nand_enabled = bool(self.config.get("nand_query_enabled", True))
        nand_kw_cfg = self.config.get("nand_trigger_keywords", list(NAND_INTENT_WORDS))
        if isinstance(nand_kw_cfg, str):
            nand_kw_cfg = [kw.strip() for kw in nand_kw_cfg.split(",") if kw.strip()]
        self.nand_trigger_keywords = self._normalize_command_keywords(
            nand_kw_cfg,
            {
                "颗粒": "查颗粒",
                "物料": "查物料",
                "flash id": "查flash id",
                "flashid": "查flashid",
                "nand": "查nand",
                "pn查询": "查pn",
                "型号查询": "查型号",
            },
            lower=True,
        )
        if not self.nand_trigger_keywords:
            self.nand_trigger_keywords = list(NAND_INTENT_WORDS)
        self.alias_to_brand = self._build_alias_map()

    def _normalize_command_keywords(self, keywords, legacy_map: dict[str, str], lower: bool = False) -> list[str]:
        normalized: list[str] = []
        for kw in keywords or []:
            item = str(kw).strip()
            if not item:
                continue
            mapped = legacy_map.get(item.lower(), item)
            if lower:
                mapped = mapped.lower()
            if mapped not in normalized:
                normalized.append(mapped)
        return normalized

    def _build_alias_map(self) -> dict[str, str]:
        alias_map = {}
        for brand_id, aliases in self.brand_aliases.items():
            for alias in aliases:
                alias = str(alias).strip().lower()
                if alias:
                    alias_map[alias] = str(brand_id)
        return alias_map

    def _match_strict_keyword(self, text: str, keywords: list[str], allow_empty_arg: bool = False) -> Optional[tuple[str, str]]:
        """匹配命令。默认要求“关键词 空格 参数”；有图时允许只有关键词。"""
        clean = self._clean_text(text)
        for keyword in sorted((str(kw).strip() for kw in keywords), key=len, reverse=True):
            if not keyword:
                continue
            if allow_empty_arg and re.fullmatch(re.escape(keyword), clean, re.I):
                return keyword, ""
            match = re.match(rf"^{re.escape(keyword)}\s+(.+)$", clean, re.I)
            if match:
                arg = match.group(1).strip()
                if arg:
                    return keyword, arg
        return None

    def _has_intent(self, text: str, allow_empty_arg: bool = False) -> bool:
        return self._match_strict_keyword(text, list(self.trigger_keywords), allow_empty_arg) is not None

    def _has_nand_intent(self, text: str, allow_empty_arg: bool = False) -> bool:
        return self._match_strict_keyword(text, self.nand_trigger_keywords, allow_empty_arg) is not None

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

    def _parse_nand_query(self, text: str, allow_empty_arg: bool = False) -> Optional[NandQuery]:
        """解析 NAND 物料查询请求。无文本参数且有图时交给视觉模型。"""
        if not self.nand_enabled:
            return None
        matched = self._match_strict_keyword(text, self.nand_trigger_keywords, allow_empty_arg)
        if not matched:
            return None
        _, tmp = matched
        if allow_empty_arg and tmp.strip().lower() in {"", "图片", "图", "image", "img"}:
            return None
        # 去掉可能的空格分隔（flash id 可能是 "2C,64,44,4B,A9,00" 或 "2C 64 44 4B A9 00"）
        cleaned = re.sub(r"[\s,]+", "", tmp.strip())
        if not cleaned:
            return None
        # Flash ID 通常是纯 hex，12-16 位
        # Part Number 通常包含字母和数字混合，如 MT29F...
        if re.fullmatch(r"[0-9A-Fa-f]{4,16}", cleaned):
            return NandQuery(query=cleaned.upper(), query_type="flash_id")
        # 默认当 PN 处理
        return NandQuery(query=cleaned.upper(), query_type="part_number")

    def _parse_query(self, text: str, allow_empty_arg: bool = False) -> Optional[WarrantyQuery]:
        matched = self._match_strict_keyword(text, list(self.trigger_keywords), allow_empty_arg)
        if not matched:
            return None
        _, arg = matched
        if allow_empty_arg and arg.strip().lower() in {"", "图片", "图", "image", "img"}:
            return None
        match = re.match(r"^(\S+)\s+([A-Za-z0-9][A-Za-z0-9\-_]{%d,%d})\s*$" % (self.min_sn_len - 1, self.max_sn_len - 1), arg, re.I)
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

    def _log_value(self, value: str) -> str:
        return str(value or "")

    def _log_text(self, text: str) -> str:
        return self._clean_text(text)

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

    async def _result_message(self, event: AstrMessageEvent, text: str, card: Optional[dict] = None):
        """t2i 开启且渲染成功时发图片卡片，否则回退纯文本。"""
        if card:
            if not (self.t2i_enabled and self._report_template):
                return event.plain_result(text)
            try:
                url = await self.html_render(self._report_template, card, return_url=True)
            except Exception as exc:
                logger.warning("[online_hardware] t2i render failed: %s", exc)
                return event.plain_result(text)
            if url:
                return event.image_result(str(url))
        return event.plain_result(text)

    def _warranty_card(self, result: WarrantyResult, from_cache: bool = False) -> dict:
        ok = result.ok and not result.need_manual
        title = result.brand
        if result.model and result.model != "未知":
            title = f"{result.brand} {result.model}"
        fields = [{"label": "序列号", "value": result.serial}]
        if result.expire_date and result.expire_date != "未知":
            fields.append({"label": "质保到期", "value": result.expire_date})
        if result.model and result.model != "未知":
            fields.append({"label": "型号", "value": result.model})
        if result.region and result.region != "未知":
            fields.append({"label": "地区", "value": REGION_DISPLAY.get(result.region, result.region)})
        return {
            "accent": T2I_ACCENTS["ok"] if ok else T2I_ACCENTS["bad"],
            "eyebrow": "质保查询",
            "status": result.status or "未知",
            "title": title,
            "subtitle": "",
            "fields": fields,
            "notes_title": "结果说明",
            "notes": [result.message] if result.message else [],
            "footer_left": "来源：" + (result.source_url or "厂商质保接口"),
            "footer_right": "本地缓存：命中缓存" if from_cache else "本地缓存：本次查询",
        }

    def _nand_card(self, result) -> dict:
        ok = bool(result.ok)
        fields = []
        if ok:
            fields.append({"label": "查询", "value": result.query})
            if result.vendor:
                fields.append({"label": "厂商", "value": result.vendor})
            if result.chip_kind:
                fields.append({"label": "类型", "value": result.chip_kind})
            for block in result.blocks:
                if block.id == "controllers":
                    continue
                for item in block.fields:
                    if item.display and item.key != "controller" and len(fields) < 6:
                        fields.append({"label": item.label or item.key, "value": item.display})
        return {
            "accent": T2I_ACCENTS["ok"] if ok else T2I_ACCENTS["bad"],
            "eyebrow": "NAND 物料查询",
            "status": "查询成功" if ok else "查询失败",
            "title": (result.part_number or result.query or "NAND 查询"),
            "subtitle": result.subtitle if ok else "",
            "fields": fields,
            "notes_title": "结果说明",
            "notes": [] if ok else [result.error or "查询失败"],
            "footer_left": "来源：FlashMaster",
            "footer_right": "iTXTech fdnext",
        }

    def _inspection_card(self, report) -> dict:
        # 只展示结论徽标 + 疑点与依据，不与模型完整报告混排。
        verdict = report.verdict or "证据不足"
        notes = [item.strip() for item in report.reason().split("；") if item.strip()]
        merged = []
        for item in notes:
            if merged and merged[-1].endswith(("：", ":")):
                merged[-1] = merged[-1] + item
            else:
                merged.append(item)
        notes = merged[:6]
        if not notes:
            notes = ["未取得可解析的疑点与依据。"]
        return {
            "accent": VERDICT_ACCENTS.get(verdict, T2I_ACCENTS["muted"]),
            "eyebrow": "硬盘图片分析",
            "status": verdict,
            "title": report.card_title(),
            "subtitle": "",
            "fields": [],
            "notes_title": "疑点与依据",
            "notes": notes,
            "footer_left": "建议补拍完整标签" if verdict == "证据不足" else "Gemini 看图 + Google 搜索",
            "footer_right": "非实物鉴定结论",
        }

    def _extract_images(self, event: AstrMessageEvent) -> list[str]:
        images: list[str] = []

        def add_image(comp) -> None:
            url = getattr(comp, "url", None)
            file = getattr(comp, "file", None)
            path = getattr(comp, "path", None)
            if url:
                images.append(str(url))
            elif path:
                images.append(str(path))
            elif file:
                images.append(str(file))

        try:
            for comp in getattr(event.message_obj, "message", []) or []:
                if hasattr(comp, "chain") and getattr(comp, "chain", None):
                    for quoted_comp in comp.chain:
                        add_image(quoted_comp)
                add_image(comp)
        except Exception:
            pass
        return images

    async def _call_vision_model(self, prompt: str, image_url: str) -> str:
        return await self.vision_client.complete(prompt, [image_url])

    async def _query_from_images(self, event: AstrMessageEvent, text: str) -> Optional[WarrantyQuery]:
        if not self.image_recognition_enabled:
            return None
        images = self._extract_images(event)
        if not images:
            return None
        return await self._query_from_images_llm(event, images, text)

    async def _query_from_images_llm(self, event: AstrMessageEvent, images: list[str], text: str) -> Optional[WarrantyQuery]:
        prompt = self.image_prompt or (
            "从图片里的产品标签提取品牌和序列号SN。"
            "只返回JSON，不要解释，格式："
            "{\"brand\":\"western_digital或seagate或toshiba或sandisk或ymtc或unknown\",\"serial\":\"SN\"}。"
            "如果识别不到serial就返回空字符串。"
        )
        prompt = f"{prompt}\n用户文本：{text}"
        raw = await self._call_vision_model(prompt, images[0])
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
        if not self.image_recognition_enabled:
            return None
        images = self._extract_images(event)
        if not images:
            return None

        prompt = self.nand_image_prompt or (
            "从图片中的 NAND Flash 芯片丝印提取完整 Part Number 或 Flash ID。"
            "优先识别厂商型号，如 MT29F、K9、H27、TC58 等；不要把日期码、批次码、封装码或控制器型号当作 PN。"
            "只返回JSON，不要解释。"
            "格式：{\"pn\":\"完整型号字符串\",\"type\":\"pn或flash_id\"}。"
            "无法确认时返回 {\"pn\":\"\",\"type\":\"\"}。"
        )
        prompt = f"{prompt}\n用户文本：{text}"
        raw = await self._call_vision_model(prompt, images[0])
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

    @filter.event_message_type(filter.EventMessageType.ALL, priority=20)
    async def on_message(self, event: AstrMessageEvent):
        if not bool(getattr(event, "is_at_or_wake_command", False)):
            return
        try:
            text = event.message_str or event.get_message_str() or ""
        except Exception:
            text = ""
        images = self._extract_images(event)
        has_images = bool(images)
        inspection_requested = self._clean_text(text) == "查硬盘"
        if self.hardware_inspection_enabled and inspection_requested:
            event.should_call_llm(False)
            images = list(dict.fromkeys(images))
            if not 1 <= len(images) <= 3:
                yield event.plain_result("请发送或引用同一块机械硬盘的 1–3 张照片，再使用“查硬盘”。")
                return
            card = None
            try:
                report = await self.hardware_inspector.analyze(images)
                reply = report.render()
                card = self._inspection_card(report)
            except InspectionError as exc:
                reply = str(exc)
                logger.warning("[online_hardware] inspection failed: %s", reply)
            except Exception:
                reply = "硬盘分析失败，请检查接口配置后重试。"
                logger.warning("[online_hardware] unexpected inspection failure")
            yield await self._result_message(event, reply, card)
            return
        strict_nand_intent = self._has_nand_intent(text, allow_empty_arg=has_images)
        strict_warranty_intent = self._has_intent(text, allow_empty_arg=has_images)
        logger.debug(
            "[online_hardware] inspect at=%s images=%d nand_intent=%s warranty_intent=%s text=%s",
            bool(getattr(event, "is_at_or_wake_command", False)),
            len(images),
            strict_nand_intent,
            strict_warranty_intent,
            self._log_text(text),
        )
        if not strict_nand_intent and not strict_warranty_intent:
            return
        mode = "nand" if strict_nand_intent else "warranty"
        logger.info("[online_hardware] command hit mode=%s images=%d text=%s", mode, len(images), self._log_text(text))

        # 优先检查 NAND 物料查询意图
        nand_query = self._parse_nand_query(text, allow_empty_arg=has_images)
        if not nand_query and strict_nand_intent:
            # 严格命中 NAND 关键词但文本参数无法直接查询时，尝试从图片识别
            try:
                nand_query = await self._query_nand_from_images(event, text)
            except Exception as e:
                logger.warning(f"[online_hardware] nand image extract failed: {e}")
                if self.fail_message:
                    yield event.plain_result(self.fail_message)
                event.should_call_llm(False)
                return
        if nand_query:
            logger.info("[online_hardware] nand query type=%s query=%s", nand_query.query_type, self._log_value(nand_query.query))
            card = None
            try:
                nand_result = await self.nand_provider.query(nand_query)
                from .providers.nand import NandProvider
                reply = NandProvider.format_result(nand_result)
                card = self._nand_card(nand_result)
            except Exception as e:
                logger.warning(f"[online_hardware] nand query failed: {e}")
                reply = f"NAND 物料查询失败: {e}"
            yield await self._result_message(event, reply, card)
            event.should_call_llm(False)
            return

        # 质保查询流程
        query = self._parse_query(text, allow_empty_arg=has_images)
        if not query and strict_warranty_intent:
            try:
                query = await self._query_from_images(event, text)
            except Exception as e:
                logger.warning(f"[online_hardware] image sn extract failed: {e}")
                if self.fail_message:
                    yield event.plain_result(self.fail_message)
                event.should_call_llm(False)
                return
        if not query:
            logger.info("[online_hardware] command hit but no query parsed images=%d text=%s", len(images), self._log_text(text))
            event.should_call_llm(False)
            return

        logger.info("[online_hardware] warranty query brand=%s region=%s sn=%s", query.brand, query.region, self._log_value(query.serial))
        key = self._cache_key(query)
        cached = self.cache.get(key)
        if cached:
            logger.info("[online_hardware] warranty cache hit brand=%s region=%s sn=%s", query.brand, query.region, self._log_value(query.serial))
            yield await self._result_message(event, self._format_result(cached, from_cache=True), self._warranty_card(cached, from_cache=True))
            event.should_call_llm(False)
            return

        provider = self.providers.get(query.brand)
        if not provider:
            return

        try:
            result = await provider.query(query)
        except Exception as e:
            logger.warning("[online_hardware] query failed brand=%s sn=%s: %s", query.brand, self._log_value(query.serial), e)
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
        yield await self._result_message(event, reply, self._warranty_card(result))
        event.should_call_llm(False)
