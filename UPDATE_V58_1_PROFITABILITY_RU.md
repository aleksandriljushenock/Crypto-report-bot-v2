# v58.1 — profitability-first validation

- BREAKOUT|LONG: добавлена anchored walk-forward проверка экономической устойчивости на 3 последовательных временных фолдах.
- Порог expected-return фиксируется до walk-forward; тестовые фолды не используются для его подбора.
- Economic champion допускается только когда classifier проходит прежние AUC/Brier/Precision gates, selection+champion utility положительны, champion PF проходит минимум, и walk-forward подтверждает прибыльность.
- MAE/Spearman не удалены: они остаются диагностикой и legacy-путем. Economic path не требует точного прогноза величины return, если отбор сделок устойчиво прибыльный OOS.
- GLOBAL и PULLBACK не могут получить economic champion через новый путь: он разрешен только BREAKOUT|LONG.
- Fail-closed: при нехватке данных/сделок или отрицательном fold модель остается shadow.
- Реальная торговля автоматически не включается этим релизом; статус модели лишь разрешает validated prediction path.
