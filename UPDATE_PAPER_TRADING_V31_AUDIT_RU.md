# Paper Trading v31 — execution/liquidation/statistics audit

## Что было найдено

1. **Liquidation вообще не участвовала в lifecycle открытой позиции.**
   `estimated_liquidation_price` рассчитывалась и показывалась в Telegram, но `update_positions()` проверял только SL/TP/TIME_EXIT.

2. **Можно было пропустить событие внутри текущей 5m свечи.**
   Kline API возвращает время открытия свечи. Старый код сравнивал его с `last_checked_at`. Если проверка была в 12:03, то на следующем цикле свеча 12:00–12:05 уже отбрасывалась, хотя stop/liquidation могли произойти в 12:04.

3. **После API hiccup/redeploy могла оставаться stale OPEN позиция.**
   Не было live-price sanity check, который немедленно закрывает позицию, если рынок уже прошёл liquidation/SL/TP.

4. **Liquidation PnL мог быть неверным при попытке закрывать обычной формулой.**
   Для isolated-margin симуляции убыток теперь ограничен зарезервированной маржой (+ уже уплаченная entry fee), а не может стать больше collateral из-за дополнительного simulated slippage.

5. **Статистика смешивала zero-PnL с убыточными сделками.**
   Теперь breakeven — отдельная категория.

6. **В Telegram `Текущий баланс` фактически был free balance.**
   При открытых позициях он уменьшался на зарезервированную маржу и выглядел как убыток. Теперь отдельно показываются `Свободный баланс` и `Realized equity`.

7. **Статистика зависела от mutable account aggregate.**
   После сбоя между закрытием позиции и обновлением account row мог возникнуть drift. v31 рассчитывает контрольные free balance/equity из immutable trade ledger + open positions и показывает расхождение.

## Новая execution логика

- tracking предпочитает 1m candles и fallback на 5m;
- окно свечей перекрывается между циклами, поэтому незакрытая свеча не теряется;
- дополнительно проверяется текущая market price;
- если candle range достигает liquidation level, используется консервативный `LIQUIDATION_CONSERVATIVE`;
- если candle открылась уже за liquidation — `LIQUIDATION_GAP`;
- если текущая цена уже за liquidation — `LIQUIDATION`;
- liquidation фиксируется в `execution_audit` (`liquidation_breached`, `liquidation_hit_at`, `liquidation_hit_price`);
- Telegram присылает отдельное `💥 PAPER POSITION LIQUIDATED`.

## Статистика

Добавлены:
- wins / losses / breakeven;
- TP / SL / Liquidation / TIME_EXIT counts;
- ROI;
- derived realized equity;
- derived free balance;
- ledger/account drift indicator.

## Рекомендуемая настройка Railway

```env
PAPER_UPDATE_INTERVAL_MINUTES=1
PAPER_EXECUTION_KLINE_LIMIT=1000
```

Если `PAPER_UPDATE_INTERVAL_MINUTES=5` уже есть в Railway, заменить на `1`.

Новая SQL-миграция не нужна: данные liquidation audit сохраняются в существующем `paper_positions.execution_audit` JSONB.
