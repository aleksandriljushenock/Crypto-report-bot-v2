# V51 Runtime hardening

- Добавлены bounded retry/backoff для MEXC Futures на HTTP 429/500/502/503/504 и сетевые ошибки.
- 404 по-прежнему немедленно классифицируется как unsupported symbol, без бессмысленных retry.
- Удалены случайные пустые артефакты `None`, `int`, `0.5).astype(int)` из production-сборки.
- Добавлены регрессионные тесты MEXC Futures transient recovery и fail-fast для 404.
- Полный regression suite и compileall должны выполняться перед упаковкой.
