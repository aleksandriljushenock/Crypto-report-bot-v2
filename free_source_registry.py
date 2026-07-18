import os
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class FeedSource:
    name: str
    url: str
    category: str = "news"
    enabled: bool = True


DEFAULT_NEWS_FEEDS = (
    FeedSource("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    FeedSource("Cointelegraph", "https://cointelegraph.com/rss"),
    FeedSource("Decrypt", "https://decrypt.co/feed"),
    FeedSource("Bitcoin Magazine", "https://bitcoinmagazine.com/.rss/full/"),
)

DEFAULT_SMART_MONEY_FEEDS = (
    FeedSource("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", "smart_money"),
    FeedSource("Cointelegraph", "https://cointelegraph.com/rss", "smart_money"),
    FeedSource("Decrypt", "https://decrypt.co/feed", "smart_money"),
)


def _custom_urls(variable: str) -> list[str]:
    return [value.strip() for value in os.getenv(variable, "").split(",") if value.strip()]


def get_news_feeds() -> list[FeedSource]:
    custom = _custom_urls("NEWS_RSS_FEEDS")
    if custom:
        return [FeedSource(f"Custom {index}", url) for index, url in enumerate(custom, 1)]
    if os.getenv("NEWS_USE_DEFAULT_FEEDS", "true").strip().lower() in {"0", "false", "no", "off"}:
        return []
    return list(DEFAULT_NEWS_FEEDS)


def get_smart_money_feeds() -> list[FeedSource]:
    custom = _custom_urls("SMART_MONEY_FEEDS")
    if custom:
        return [FeedSource(f"Custom {index}", url, "smart_money") for index, url in enumerate(custom, 1)]
    if os.getenv("SMART_MONEY_USE_DEFAULT_FEEDS", "true").strip().lower() in {"0", "false", "no", "off"}:
        return []
    return list(DEFAULT_SMART_MONEY_FEEDS)


def source_names(sources: Iterable[FeedSource]) -> str:
    return ", ".join(source.name for source in sources)
