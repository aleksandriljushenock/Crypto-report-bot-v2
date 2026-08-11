# Strategy Lab v25

Добавлен отдельный раздел Telegram `🧭 Стратегии`.

Первая стратегия: **Fib 0.5 Pullback**.

Логика:
- USDT perpetual с 24h quote volume >= $100M;
- D1 significant swing и восходящий trend;
- Fib 0.5;
- отдельная structural support-zone рядом с 0.5;
- H4 confirmation;
- Entry около support/Fib;
- SL ниже support с ATR buffer;
- TP перед D1 swing high;
- R/R >= 2 для статуса READY.

Меню стратегии:
- Анализировать монеты;
- Анализ Win Rate;
- Кандидаты;
- История;
- Правила стратегии;
- Обновить outcomes.

Стратегия намеренно не смешивается с основным Scanner/Paper Trading. Она ведёт собственный журнал в Supabase, чтобы сначала доказать edge на реальных forward-наблюдениях.

Перед deploy один раз выполнить `migrations/SUPABASE_STRATEGY_LAB_V25.sql`.
