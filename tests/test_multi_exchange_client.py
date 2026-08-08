import unittest

from multi_exchange_client import MarketListing, aggregate_markets


class MultiExchangeAggregationTests(unittest.TestCase):
    def test_merges_exchanges_and_prefers_liquidity(self):
        rows = [
            MarketListing("mexc", "ABCUSDT", "ABC", "USDT", last_price=1.0, quote_volume_24h=100),
            MarketListing("gate", "ABC_USDT", "ABC", "USDT", last_price=1.1, quote_volume_24h=300),
            MarketListing("okx", "ABC-USDT", "ABC", "USDT", last_price=1.2, quote_volume_24h=200),
        ]
        result = aggregate_markets(rows)
        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertEqual(item["exchangeCount"], 3)
        self.assertEqual(item["primaryExchange"], "gate")
        self.assertEqual(item["lastPrice"], 1.1)
        self.assertEqual(item["quoteVolume24h"], 600)
        self.assertEqual(item["pairs"]["okx"], "ABC-USDT")


if __name__ == "__main__":
    unittest.main()
