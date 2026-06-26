import asyncio
import io
import json
import re
import time

import requests

from .base import WarrantyProvider
from ..models import WarrantyQuery, WarrantyResult


class YmtcProvider(WarrantyProvider):
    brand_id = "ymtc"
    display_name = "致态"
    source_url = "https://www.ymtc.com/cn/salesupport.html"

    def __init__(self, timeout: int = 15, image_sn_mode: str = "llm"):
        super().__init__(timeout)
        self.image_sn_mode = image_sn_mode
        self.max_retry = 5
        self.retry_delay = 3  # 每次重试间隔3秒

    async def query(self, query: WarrantyQuery) -> WarrantyResult:
        return await asyncio.to_thread(self._query_sync, query)

    def _query_sync(self, query: WarrantyQuery) -> WarrantyResult:
        session = requests.Session()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
            "Referer": "https://www.ymtc.com/cn/salesupport.html",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

        for attempt in range(self.max_retry):
            try:
                # 重试间隔
                if attempt > 0:
                    time.sleep(self.retry_delay)

                # 1. 获取验证码图片
                captcha_url = "https://www.ymtc.com/cn/info/captchas?t=SN"
                captcha_resp = session.get(captcha_url, headers=headers, timeout=self.timeout)
                
                # 429限流处理
                if captcha_resp.status_code == 429:
                    continue
                    
                captcha_resp.raise_for_status()

                # 检查是否是图片
                content_type = captcha_resp.headers.get("Content-Type", "")
                if "image" not in content_type and len(captcha_resp.content) < 100:
                    continue

                # 2. 识别验证码
                verify_code = self._recognize_captcha(captcha_resp.content)
                if not verify_code or len(verify_code) < 4:
                    continue

                # 3. 查询质保
                query_url = f"https://www.ymtc.com/cn/info/getsn?snCode={query.serial}&verifyCode={verify_code}"
                resp = session.get(query_url, headers=headers, timeout=self.timeout)
                
                # 429限流处理
                if resp.status_code == 429:
                    continue
                    
                resp.raise_for_status()

                data = resp.json()
                if data.get("code") == 200:
                    msg = data.get("msg", "")
                    nums = data.get("data", {}).get("nums", 0)
                    status = "正品" if nums > 0 else "非正品"

                    return WarrantyResult(
                        ok=True,
                        brand=self.display_name,
                        serial=query.serial.upper(),
                        model="致态SSD",
                        status=status,
                        expire_date="未知",
                        region=query.region,
                        message=msg,
                        source_url=self.source_url,
                        raw=data,
                        need_manual=False,
                    )
                else:
                    # 验证码错误，重试
                    continue

            except Exception as e:
                if attempt == self.max_retry - 1:
                    return self._manual(query, f"YMTC官网接口请求失败：{e}")
                continue

        return self._manual(query, f"验证码识别失败（已重试{self.max_retry}次），请稍后再试。")

    def _recognize_captcha(self, image_data: bytes) -> str:
        """识别验证码"""
        try:
            import ddddocr
            ocr = ddddocr.DdddOcr(show_ad=False)
            result = ocr.classification(image_data)
            return result.strip() if result else ""
        except ImportError:
            return ""
        except Exception:
            return ""

    def _manual(self, query: WarrantyQuery, message: str) -> WarrantyResult:
        return WarrantyResult(
            ok=False,
            brand=self.display_name,
            serial=query.serial.upper(),
            model="",
            status="",
            expire_date="",
            region=query.region,
            message=message,
            source_url=self.source_url,
            raw={},
            need_manual=True,
        )
