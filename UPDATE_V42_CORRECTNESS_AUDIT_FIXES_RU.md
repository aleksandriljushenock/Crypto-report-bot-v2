# V42 — Full correctness audit fixes

Версия закрывает баги полного аудита V41.

## Исправлено
- V14: финальный chronological holdout отделяется до любого global/specialist optimizer; leakage устранён.
- Learning store: при лимите берутся самые свежие resolved observations и возвращаются в хронологическом порядке.
- Adaptive Paper model: обучается на самых свежих closed positions.
- Adaptive Champion: candidate сравнивается с текущим Champion на том же validation set; при ошибке чтения Champion promotion fail-closed.
- Paper Trading: last_checked_at двигается только до реально покрытого завершёнными свечами времени.
- Paper Trading: pending expiration и TIME_EXIT не выполняются до покрытия deadline историей рынка.
- Paper Trading: live ticker не используется при дыре/отставании execution history.
- Strategy Lab: entry candle больше не может одновременно дать TP/SL; outcome tracking начинается со следующей закрытой свечи.
- Strategy Lab: текущая незакрытая 1H свеча исключена из forward tracking.
- Trade Monitor: Paper registration выполняется независимо от успешности Telegram delivery.
- Adaptive Cloud Overlay: реально применяется поверх V14 и затем повторно проходит operator weight policy.
- Learning MAX 2: V14 calibration применяется только к score, на котором calibration была обучена; финальная probability строится после корректной калибровки компонента.
- Learning MAX 2: исправлен приоритет marketRegime при пустых features.
- Min EV: безопасный fallback унифицирован на 2.0%, .env.example исправлен.
- Discovery: menu_discovery открывает Discovery submenu вместо немедленного запуска Early Discovery.
- Legacy sklearn champion.pkl: заменён compatibility facade на production V14, чтобы старые cron/imports не обучали мёртвую модель.
- Auto Learning: scheduled learning fail-closed при недоступности model-control DB.
- Profit Profile: запись JSON через temp + fsync + atomic os.replace.

## Проверка
- pytest -W error: 140 passed
- compileall: OK
