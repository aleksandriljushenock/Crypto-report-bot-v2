# Strategy Lab v27 — Smart Money + Auto Scheduler

## Добавлено
- Новая стратегия `Smart Money Confluence` (SMC): H4 liquidity sweep/reclaim, H1 BOS/CHoCH/rejection, Order Block, FVG, premium/discount и optional Funding/OI.
- SMC использует confluence: один Order Block или один FVG сам по себе не создаёт READY.
- Strategy Lab теперь может запускаться автоматически по таймеру.
- Безопасный режим по умолчанию — `round_robin`: каждые 30 минут запускается одна стратегия. При 12 стратегиях полный круг занимает около 6 часов.
- Автоскан пропускается, если занят основной Deep Scan или другой Strategy Lab scan.
- Worker проходит через общий heavy-task lock AutomationSupervisor, поэтому не конкурирует с Chronos/Optimizer/другими тяжёлыми задачами.
- Outcomes обновляются перед каждым strategy scan как и раньше.

## Рекомендуемые ENV
```env
STRATEGY_LAB_AUTO_ENABLED=true
STRATEGY_LAB_AUTO_INTERVAL_MINUTES=30
STRATEGY_LAB_AUTO_MODE=round_robin
STRATEGY_LAB_AUTO_NOTIFY_READY=false
STRATEGY_LAB_H1_LIMIT=260
```

`all` mode поддерживается, но на Railway не рекомендуется: он запускает все стратегии последовательно в один тик и создаёт длинную тяжёлую задачу.
