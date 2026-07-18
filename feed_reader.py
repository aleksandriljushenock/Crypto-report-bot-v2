import html
import time
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


_SESSION = requests.Session()
_RETRY = Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=0.8,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset({"GET"}),
    respect_retry_after_header=True,
)
_SESSION.mount("https://", HTTPAdapter(max_retries=_RETRY, pool_connections=10, pool_maxsize=10))
_SESSION.mount("http://", HTTPAdapter(max_retries=_RETRY, pool_connections=10, pool_maxsize=10))


def _clean(value: str) -> str:
    soup = BeautifulSoup(html.unescape(value or ""), "html.parser")
    return " ".join(soup.get_text(" ", strip=True).split())


def read_feed(url: str, limit: int = 50) -> list[dict[str, Any]]:
    response = _SESSION.get(
        url,
        timeout=(8, 30),
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; CryptoReportService/9.0; +personal-research)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        },
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "xml")
    nodes = soup.find_all("item") or soup.find_all("entry")
    rows: list[dict[str, Any]] = []
    for node in nodes[: max(1, int(limit))]:
        title_node = node.find("title")
        link_node = node.find("link")
        description_node = node.find("description") or node.find("summary") or node.find("content")
        date_node = node.find("pubDate") or node.find("published") or node.find("updated")
        link = ""
        if link_node:
            link = link_node.get("href") or link_node.get_text(strip=True)
        title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
        description = _clean(description_node.get_text(" ", strip=True) if description_node else "")
        if not title:
            continue
        rows.append(
            {
                "title": title,
                "description": description[:1200],
                "link": link,
                "published": _clean(date_node.get_text(" ", strip=True) if date_node else ""),
                "fetchedAt": datetime.now(timezone.utc).isoformat(),
            }
        )
    return rows
