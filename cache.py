import json
import time
from pathlib import Path
from typing import Optional

from .models import WarrantyResult


class JsonCache:
    def __init__(self, path: Path, ttl_seconds: int):
        self.path = path
        self.ttl_seconds = ttl_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self, data: dict):
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, key: str) -> Optional[WarrantyResult]:
        item = self._load().get(key)
        if not item:
            return None
        if time.time() - float(item.get("created_at", 0)) > self.ttl_seconds:
            return None
        payload = item.get("result", {})
        return WarrantyResult(**payload)

    def set(self, key: str, result: WarrantyResult):
        data = self._load()
        data[key] = {"created_at": time.time(), "result": result.__dict__}
        self._save(data)
