# V43.0.0 — Full Integrity Audit

V43 собрана после повторного полного аудита V42. Цель релиза — fail-closed фильтрация сигналов, консервативная event-time симуляция Paper Trading, зрелые и сопоставимые learning labels, атомарное хранение Adaptive Champion и устранение скрытых/мёртвых контуров.

## Критические исправления

1. Scanner теперь fail-closed: исключение финального AI/Hedge ranking возвращает 0 сигналов и событие SIGNAL_RANKING_FAILED вместо отправки предварительных кандидатов без финального Quality/EV gate.
2. Глобальные TRADE_MIN_SCORE / TRADE_MIN_RR / TRADE_MIN_PROBABILITY являются жёстким floor. Профиль PULLBACK/BREAKOUT/MOMENTUM может только ужесточить их.
3. Paper execution выбирает 1m для свежих позиций и расширяется до 5m только когда фактический lookback уже не помещается в 1000 минутных свечей.
4. Paper больше не использует boundary candle, пересекающую created/opened/pending_until/max_hold. Close после deadline не может влиять на решение до deadline.
5. Pending fill консервативно фиксируется на конце завершённой свечи; та же свеча не может одновременно дать entry и TP/SL с неизвестным порядком событий.
6. LONG_BIAS/SHORT_BIAS нормализуются в LONG/SHORT не только для Paper, но и для V14 specialists и direction-specific learning rules.

## Outcomes и обучение

7. Trade Outcome Tracker больше не использует close свечи, завершившейся после target timestamp.
8. Если историческая цена target недоступна, outcome не подменяется текущим ticker или самой старой свечой — расчёт откладывается.
9. Для старых target tracker автоматически расширяет таймфрейм 1m -> 5m -> 1h, чтобы восстановить историю после длительного простоя.
10. Klines кэшируются по symbol/interval внутри одного цикла, поэтому несколько сигналов одной монеты не создают повторный API request на каждый horizon.
11. Cloud observation остаётся pending до OUTCOME_COMPLETE_HORIZON (по умолчанию 72h), поэтому потеря локальной SQLite не прерывает восстановление будущих горизонтов.
12. V14 и Profit Profile используют один зрелый LEARNING_TARGET_HORIZON (по умолчанию 24h). Fresh 1h samples больше не сравниваются с mature 24h/72h как будто это одинаковый target.
13. latest_horizon определяется по горизонту, а не по случайному порядку observed_at.
14. Learning event получает отдельный event fingerprint по structural fingerprint + cooldown bucket. Повторный реальный сигнал через несколько часов больше не перезаписывает старое learning observation.

## Adaptive / Model Control

15. Adaptive Cloud Overlay теперь читает реальные factors из features.aiFactors / tradeProfile.
16. Snapshot Adaptive Cloud пишется атомарно temp + fsync + replace.
17. Cloud-restored V14 Champion проходит operator MANUAL/BOUNDED weight policy так же, как локальная модель.
18. Adaptive candidate/champion хранится через новую PostgreSQL RPC adaptive_model_store_v43. Archive old champion + insert new champion выполняются в одной transaction под advisory lock.
19. Если persistence Adaptive model падает, UI получает persistence-error; MODEL_PROMOTED больше не отправляется ложно.
20. Adaptive version содержит microseconds, устраняя collision при двух trainings в одну секунду.
21. После успешного candidate/promotion runtime cache Adaptive model инвалидируется сразу, а не через 300 секунд.
22. Добавлен process-wide model_training_coordinator. V14/Cloud Overlay и Adaptive Paper training не работают одновременно.
23. Learning Report теперь read-only. Просмотр /learn, Self Learning screen или Professional Report больше не запускает переобучение сам по себе.

## Profit Profile / Recency

24. Исправлен _env_bool: default=True больше не превращается в False при отсутствующей ENV. PROFILE_RECENCY_ENABLED реально включён по умолчанию.
25. Profit Profile строится только на выбранном зрелом target horizon и сохраняет target_horizon в metadata.
26. Автопересборка Profit Profile запускается через ~30 секунд после старта вместо ~15 минут, затем по обычному interval.
27. Старое поле rules, которое runtime не использовал как активные правила, переименовано в rule_diagnostics. Активные weights остаются централизованы в Hedge Engine / Telegram Strategy Settings.

## Deploy / security / release hygiene

28. .env.vps.example явно содержит LOW_MEMORY_MODE=false, поэтому VPS не выключает тяжёлые фоновые сервисы из-за скрытого default.
29. TELEGRAM_WEBHOOK_SECRET example теперь пустой; известный placeholder дополнительно отклоняется runtime даже если длиннее 24 символов.
30. Runtime SQLite DB, generated Profit Profile, scan history, logs, __pycache__, pytest cache не входят в release ZIP/Docker context.
31. Добавлены LEARNING_TARGET_HORIZON=24h и OUTCOME_COMPLETE_HORIZON=72h.

## Обязательная миграция V43

После V18/V39 выполнить:

`migrations/SUPABASE_ADAPTIVE_MODEL_V43_ATOMIC_STORE.sql`

Она создаёт SECURITY DEFINER RPC `adaptive_model_store_v43(jsonb, boolean)`, закрывает EXECUTE для public/anon/authenticated и выдаёт его только service_role.

Без этой миграции основной scanner/Paper продолжат работать, но новое Adaptive training будет возвращать `persistence-error` и не станет менять Champion — это намеренное fail-closed поведение.
