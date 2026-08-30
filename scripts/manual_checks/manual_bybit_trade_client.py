import unittest
from unittest.mock import Mock

from bybit_futures_client import BybitFuturesClient


class BybitAdapterTests(unittest.TestCase):
    def setUp(self):
        self.client = BybitFuturesClient()
        self.client._get = Mock()

    def test_exchange_info_is_binance_shaped(self):
        self.client._get.return_value = {"list": [{
            "symbol": "BTCUSDT", "quoteCoin": "USDT",
            "contractType": "LinearPerpetual", "status": "Trading"
        }]}
        result = self.client.exchange_info()
        self.assertEqual(result["symbols"][0]["status"], "TRADING")
        self.assertEqual(result["symbols"][0]["contractType"], "PERPETUAL")

    def test_klines_are_oldest_first_and_parseable(self):
        self.client._get.return_value = {"list": [
            ["200", "2", "3", "1", "2.5", "10", "25"],
            ["100", "1", "2", "0.5", "1.5", "8", "12"],
        ]}
        rows = self.client.klines("BTCUSDT", "1h", 2)
        self.assertEqual(rows[0][0], "100")
        self.assertEqual(len(rows[0]), 12)
        self.assertEqual(rows[0][7], "12")

    def test_ticker_mapping(self):
        self.client._get.return_value = {"list": [{
            "symbol": "BTCUSDT", "lastPrice": "110", "prevPrice24h": "100",
            "turnover24h": "500000000", "highPrice24h": "115", "lowPrice24h": "95",
            "fundingRate": "0.0001", "markPrice": "109", "indexPrice": "108"
        }]}
        row = self.client.ticker_24h("BTCUSDT")
        self.assertAlmostEqual(float(row["priceChangePercent"]), 10.0)
        self.assertEqual(row["quoteVolume"], "500000000.0")


if __name__ == "__main__":
    unittest.main()
