# v24 architecture map

Telegram -> `telegram_ui/router.py` -> application/services -> domain modules

Scanner:
`scanner/universe.py` -> `scanner/analysis.py` -> `scanner/signals.py` -> `scanner/pipeline.py`

Market:
`exchanges/registry.py` -> venue clients -> `trade_market_client.py` capability/fallback layer

Trading:
`trading/domain.py` -> `paper_trading.py` -> `repositories/paper_repository.py`

Runtime:
`core/runtime_config.py` + `core/runtime_state.py` + `core/events.py`

Compatibility:
`trade_engine.py` re-exports scanner API so older modules/tests keep working during migration.
