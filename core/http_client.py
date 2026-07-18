from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from core.logging_setup import get_logger
from core.settings import settings

logger = get_logger(__name__)

@dataclass
class _CacheItem:
    expires_at: float
    value: Any

class HttpClient:
    def __init__(self) -> None:
        retry = Retry(
            total=settings.http_retries,
            connect=settings.http_retries,
            read=settings.http_retries,
            status=settings.http_retries,
            backoff_factor=settings.http_backoff,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD", "OPTIONS", "POST"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=30, pool_maxsize=60)
        self.session = requests.Session()
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update({"User-Agent": "crypto-report-service-v10/1.0"})
        self._cache: dict[str, _CacheItem] = {}
        self._lock = threading.Lock()

    @property
    def timeout(self) -> tuple[int, int]:
        return settings.http_connect_timeout, settings.http_read_timeout

    def request(self, method: str, url: str, *, timeout=None, raise_for_status=True, **kwargs):
        started = time.monotonic()
        try:
            response = self.session.request(method, url, timeout=timeout or self.timeout, **kwargs)
            if raise_for_status:
                response.raise_for_status()
            logger.debug("HTTP %s %s -> %s in %.2fs", method, url, response.status_code, time.monotonic() - started)
            return response
        except requests.RequestException:
            logger.exception("HTTP %s failed: %s", method, url)
            raise

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)

    def get_json(self, url: str, *, params=None, cache_ttl: Optional[int] = None, **kwargs):
        ttl = settings.cache_ttl_seconds if cache_ttl is None else max(0, cache_ttl)
        key = f"{url}|{repr(sorted((params or {}).items()))}"
        now = time.monotonic()
        if ttl:
            with self._lock:
                item = self._cache.get(key)
                if item and item.expires_at > now:
                    return item.value
        value = self.get(url, params=params, **kwargs).json()
        if ttl:
            with self._lock:
                self._cache[key] = _CacheItem(now + ttl, value)
        return value

http = HttpClient()
