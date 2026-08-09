# Deploy v24 на Railway

1. Сделайте backup текущего проекта/таблиц Paper Trading.
2. В Supabase SQL Editor выполните `migrations/SUPABASE_FINAL_REFACTOR_V24.sql` один раз. Скрипт идемпотентный и допускает повторный запуск.
3. Загрузите содержимое v24 в GitHub. Не загружайте `.env`.
4. Проверьте Railway Variables. Минимально нужны Telegram/Supabase secrets и ваши runtime settings. `TRADE_MARKET_PROVIDERS` может оставаться списком из 10 бирж.
5. Redeploy Railway.
6. После запуска в Telegram проверьте: `Состояние`, `Health`, `Биржи`, `Сканер`, `Paper Trading`, `AI Optimizer`, Chronos toggle.
7. Первый полный scan должен пройти `Universe -> market data -> analysis -> hedge -> finalizing`; статус должен совпадать во всех экранах.
8. В Paper Trading новый сигнал сначала должен получить `pending_entry`, если реальная цена ещё не достигла entry. PnL появляется только после actual fill и закрытия.

Если старый Paper account имеет расхождения после прежних phantom/duplicate fills, используйте `repositories.paper_reconciliation.reconcile(..., apply=False)` для dry-run. Применять repair следует только после просмотра результата.
