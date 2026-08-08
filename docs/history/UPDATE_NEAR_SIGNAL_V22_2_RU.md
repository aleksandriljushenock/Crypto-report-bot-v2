# v22.2 — Near-Signal Distance Scoring

Исправлена логика Near-Signal Watchlist.

- Near Signal = ровно один непройденный числовой gate.
- Кандидат должен быть близок к порогу (`NEAR_SIGNAL_MIN_DISTANCE_PCT`, default 85%).
- `anti-profile`/hard block не считается Near Signal и остаётся только в Shadow.
- Не рассчитанные Quality/EV отображаются как `—`, а не как `0`.
- Список сортируется по `distance_score`.
- После targeted re-scan кандидат удаляется из Near Watch, если перестал быть близким к сигналу.
- Старый SQLite автоматически мигрируется при запуске; SQL для Supabase не нужен.

Рекомендуемый ENV:

```env
NEAR_SIGNAL_MIN_DISTANCE_PCT=85
NEAR_SIGNAL_RESCAN_MINUTES=5
NEAR_SIGNAL_RESCAN_LIMIT=24
```
