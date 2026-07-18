# Multi-exchange discovery

The listing scan now collects public spot markets from:

- Binance
- MEXC
- Bybit
- OKX
- KuCoin
- Gate
- Bitget

No exchange API keys are required. Markets are normalized by base asset, duplicate
pairs are merged, and the most liquid venue supplies the reference price. Total
24-hour quote volume is summed across venues.

## Configuration

Add these optional variables to `.env`:

```env
ENABLED_EXCHANGES=binance,mexc,bybit,okx,kucoin,gate,bitget
EXCHANGE_QUOTES=USDT,USDC
EXCHANGE_HTTP_TIMEOUT=20
EXCHANGE_HTTP_RETRIES=4
```

An exchange failure does not stop the scan. The result includes `exchangeCounts`
and `exchangeErrors`, which are also available to the Telegram/background logging
layer.

## Database migration

`listing_database.initialize_database()` automatically adds these columns to an
existing `data/listing_database.db`:

- `primary_exchange`
- `exchange_count`
- `exchanges_json`
- `pairs_json`
- `market_type`

No manual SQL migration is required.

## Validation

```powershell
python -m compileall -q .
python -m unittest test_multi_exchange_client.py
```
