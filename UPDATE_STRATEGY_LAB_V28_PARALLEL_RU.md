# Strategy Lab v28 — параллельно с основным сканером

## Что изменено

Strategy Lab теперь может выполняться одновременно с основным Trade Scanner. По умолчанию каждый полный цикл фонового торгового монитора запускает одну следующую стратегию Strategy Lab в режиме round-robin. Стратегии между собой по-прежнему выполняются строго последовательно.

## Безопасность ресурсов

При одновременном основном скане Strategy Lab автоматически ограничивается `STRATEGY_LAB_PARALLEL_MAX_SYMBOLS` (по умолчанию 80) и делает `STRATEGY_LAB_PARALLEL_THROTTLE_MS` паузу между монетами (по умолчанию 100 мс). Это уменьшает риск rate-limit и пиков RAM.

## Railway

Рекомендуемые параметры:

```env
STRATEGY_LAB_AUTO_ENABLED=true
STRATEGY_LAB_AUTO_MODE=round_robin
STRATEGY_LAB_PARALLEL_WITH_MAIN=true
STRATEGY_LAB_SYNC_WITH_MAIN=true
STRATEGY_LAB_PARALLEL_MAX_SYMBOLS=80
STRATEGY_LAB_PARALLEL_THROTTLE_MS=100
```

Когда `STRATEGY_LAB_SYNC_WITH_MAIN=true`, отдельный таймер Strategy Lab в AutomationSupervisor отключается, чтобы не создавать дублирующие проходы. Основной Trade Monitor становится источником тактов Strategy Lab.

Новых SQL-миграций нет.
