import unittest
from unittest.mock import patch

import trade_market_client as tmc
from mexc_futures_client import MexcFuturesClient
from bingx_futures_client import BingxFuturesClient
from kucoin_futures_client import KucoinFuturesClient
from hyperliquid_futures_client import HyperliquidFuturesClient
from htx_futures_client import HtxFuturesClient


class ExtendedExchangeClientsTests(unittest.TestCase):
    def test_default_provider_order_contains_ten(self):
        with patch.dict('os.environ', {}, clear=False):
            import os
            old = os.environ.pop('TRADE_MARKET_PROVIDERS', None)
            try:
                order = tmc._provider_order()
            finally:
                if old is not None:
                    os.environ['TRADE_MARKET_PROVIDERS'] = old
        self.assertEqual(order[:5], ['binance', 'bybit', 'okx', 'bitget', 'gate'])
        for name in ['mexc', 'bingx', 'kucoin', 'hyperliquid', 'htx']:
            self.assertIn(name, order)

    def test_build_provider_new_adapters(self):
        self.assertIsInstance(tmc._build_provider('mexc', 1), MexcFuturesClient)
        self.assertIsInstance(tmc._build_provider('bingx', 1), BingxFuturesClient)
        self.assertIsInstance(tmc._build_provider('kucoin', 1), KucoinFuturesClient)
        self.assertIsInstance(tmc._build_provider('hyperliquid', 1), HyperliquidFuturesClient)
        self.assertIsInstance(tmc._build_provider('htx', 1), HtxFuturesClient)

    def test_mexc_normalization(self):
        row = MexcFuturesClient._ticker_row({'symbol':'BTC_USDT','lastPrice':'10','amount24':'1000','riseFallRate':'0.02','high24Price':'11','lower24Price':'9','holdVol':'5','fundingRate':'0.001','fairPrice':'10.1','indexPrice':'10'})
        self.assertEqual(row['symbol'], 'BTCUSDT')
        self.assertEqual(float(row['quoteVolume']), 1000)
        self.assertAlmostEqual(float(row['priceChangePercent']), 2.0)

    def test_bingx_normalization(self):
        row = BingxFuturesClient._ticker_row({'symbol':'ETH-USDT','lastPrice':'100','quoteVolume':'500','priceChangePercent':'3.2'})
        self.assertEqual(row['symbol'], 'ETHUSDT')
        self.assertEqual(float(row['quoteVolume']), 500)

    def test_kucoin_xbt_normalization(self):
        row = {'symbol':'XBTUSDTM','baseCurrency':'XBT','quoteCurrency':'USDT','lastTradePrice':'100','turnoverOf24h':'999','priceChgPct':'0.01'}
        out = KucoinFuturesClient._ticker_row(row)
        self.assertEqual(out['symbol'], 'BTCUSDT')
        self.assertAlmostEqual(float(out['priceChangePercent']), 1.0)

    def test_hyperliquid_normalization(self):
        out = HyperliquidFuturesClient._ticker_row('BTC', {'markPx':'100','prevDayPx':'80','dayNtlVlm':'1234','openInterest':'5','funding':'0.001','oraclePx':'99'})
        self.assertEqual(out['symbol'], 'BTCUSDT')
        self.assertAlmostEqual(float(out['priceChangePercent']), 25.0)

    def test_htx_normalization(self):
        out = HtxFuturesClient._ticker_row({'contract_code':'SOL-USDT','close':'20','open':'10','amount':'3'})
        self.assertEqual(out['symbol'], 'SOLUSDT')
        self.assertAlmostEqual(float(out['quoteVolume']), 60.0)


if __name__ == '__main__':
    unittest.main()
