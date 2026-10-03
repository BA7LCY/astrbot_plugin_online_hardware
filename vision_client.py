from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Iterable


def normalize_provider_ids(value: Any) -> list[str]:
    """Normalize a provider selector value while preserving its configured order."""
    if isinstance(value, str):
        value = value.replace(";", ",").split(",")
    if not isinstance(value, Iterable) or isinstance(value, (bytes, bytearray)):
        return []

    result: list[str] = []
    for item in value:
        provider_id = str(item or "").strip()
        if provider_id and provider_id not in result:
            result.append(provider_id)
    return result


@dataclass(frozen=True)
class VisionAttempt:
    provider_id: str
    error: str


class VisionProviderError(RuntimeError):
    """Raised after every configured plugin vision provider has failed."""

    def __init__(self, attempts: list[VisionAttempt]):
        super().__init__("插件视觉模型调用失败")
        self.attempts = attempts


class VisionClient:
    """Stateless plugin-owned vision calls with ordered provider fallback."""

    def __init__(
        self,
        context: Any,
        provider_ids: Iterable[str],
        *,
        timeout_seconds: float = 60,
        logger: Any = None,
    ) -> None:
        self.context = context
        self.provider_ids = normalize_provider_ids(provider_ids)
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        self.logger = logger

    async def complete(self, prompt: str, image_urls: list[str]) -> str:
        attempts: list[VisionAttempt] = []
        if not self.provider_ids:
            raise VisionProviderError(attempts)

        for index, provider_id in enumerate(self.provider_ids, start=1):
            self._log(
                "info",
                "[online_hardware] vision try provider=%s attempt=%d/%d timeout=%ss",
                provider_id,
                index,
                len(self.provider_ids),
                self.timeout_seconds or "off",
            )
            try:
                provider = self.context.get_provider_by_id(provider_id)
                if inspect.isawaitable(provider):
                    provider = await provider
                text_chat = getattr(provider, "text_chat", None)
                if not callable(text_chat):
                    raise RuntimeError("provider unavailable or is not a chat provider")

                response = await self._call_provider(text_chat, prompt, image_urls)
                text = self._response_text(response)
                if not text:
                    raise RuntimeError("empty response")
                return text
            except asyncio.TimeoutError:
                error = f"timeout after {self.timeout_seconds:g}s"
            except Exception as exc:
                error = str(exc).strip() or exc.__class__.__name__

            attempts.append(VisionAttempt(provider_id=provider_id, error=error))
            self._log(
                "warning",
                "[online_hardware] vision failed provider=%s error=%s",
                provider_id,
                error,
            )

        raise VisionProviderError(attempts)

    async def _call_provider(self, text_chat: Any, prompt: str, image_urls: list[str]) -> Any:
        kwargs: dict[str, Any] = {
            "prompt": prompt,
            "image_urls": image_urls,
            # Never reuse AstrBot's ordinary conversation or image-caption history.
            "contexts": [],
        }
        if self._accepts_request_max_retries(text_chat):
            kwargs["request_max_retries"] = 1

        request = text_chat(**kwargs)
        if self.timeout_seconds > 0:
            return await asyncio.wait_for(request, timeout=self.timeout_seconds)
        return await request

    @staticmethod
    def _accepts_request_max_retries(text_chat: Any) -> bool:
        try:
            signature = inspect.signature(text_chat)
        except (TypeError, ValueError):
            return False
        return "request_max_retries" in signature.parameters or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )

    @staticmethod
    def _response_text(response: Any) -> str:
        if isinstance(response, str):
            return response.strip()

        completion_text = getattr(response, "completion_text", None)
        if completion_text:
            return str(completion_text).strip()

        result_chain = getattr(response, "result_chain", None)
        for part in getattr(result_chain, "chain", []) or []:
            text = getattr(part, "text", None)
            if text:
                return str(text).strip()
        return ""

    def _log(self, level: str, message: str, *args: Any) -> None:
        method = getattr(self.logger, level, None)
        if callable(method):
            method(message, *args)
