import time
from typing import Any, Dict, Optional


class TTLCache:
    def __init__(self, ttl_seconds: int = 900, max_size: int = 512):
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._data: Dict[str, Any] = {}

    def get(self, key: str) -> Optional[Any]:
        item = self._data.get(key)
        if not item:
            return None

        created_at, value = item
        if time.time() - created_at > self.ttl_seconds:
            self._data.pop(key, None)
            return None

        return value

    def set(self, key: str, value: Any) -> None:
        if len(self._data) >= self.max_size:
            oldest = sorted(self._data.items(), key=lambda kv: kv[1][0])[: max(1, self.max_size // 8)]
            for old_key, _ in oldest:
                self._data.pop(old_key, None)

        self._data[key] = (time.time(), value)
