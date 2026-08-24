# V41 — Model Integration & Runtime Reliability

Исправления относительно V40.2:

- Learning MAX 2.0 корректно использует `calibrated_probability()` и active V14 model; tuple `(probability, uncertainty)` больше не ломает калибровку.
- Runtime-веса из Telegram теперь влияют не только на V14 AI Score, но и на финальную Learning MAX probability.
- `Автообучение OFF` отключает и V14 Self Learning, и scheduled Adaptive Paper Model training. Ручное обучение остаётся доступно.
- Ручная кнопка обучения запускает оба обучаемых контура и показывает результаты обоих.
- Champion/Challenger экран показывает V14 и Adaptive Paper model одновременно.
- Profit Profile builder переписан на stdlib: больше нет зависимости от pandas и жёстких `/mnt/data/...` путей.
- Profit Profile может строиться прямо из Supabase на VPS и автоматически пересобирается фоновым сервисом.
- Профиль сохраняет несколько recent windows (7/14/21/30/60/90/180 дней). `PROFILE_RECENT_WINDOW_DAYS` реально выбирает окно, `PROFILE_HALF_LIFE_DAYS` реально меняет силу recency.
- Исправлена кнопка `Аналитика → Discovery`.
- Paper Trading tracker больше не делит общий heavy-task lock со scanner/optimizer; долгий scanner не блокирует минутный Paper cycle.
- Learning MAX 2 smoke test переведён в настоящий pytest test.
- Добавлены отдельные regression tests V41 для всех перечисленных исправлений.

Новые ENV (не обязательны, есть defaults):

```env
PROFIT_PROFILE_REBUILD_ENABLED=true
PROFIT_PROFILE_REBUILD_INTERVAL_MINUTES=1440
PROFILE_REBUILD_MAX_ROWS=10000
PROFILE_RECENT_WINDOWS_DAYS=7,14,21,30,60,90,180
```

Новая Supabase SQL-миграция для V41 не требуется.
