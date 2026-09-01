# V57 Profitability-First

Главная цель V57 — не повышать красивые proxy-метрики, а не допускать сигнал в Paper/Trade-контур, пока положительная прибыльность не подтверждена execution-only данными.

## Критические исправления

1. Исправлена разметка Paper fill/no-fill. `opened_at + entry_price` больше не считается доказательством исполнения. Cancelled/expired/rejected позиции всегда `PAPER_NO_FILL`; fill требует `fill_price_source`, реальную цену, время и отсутствие invalid/synthetic признаков.
2. Новый `execution_training_dataset_v57`. V56-лейблы намеренно не копируются: dataset строится заново из Paper + replay Shadow.
3. Profit Profile разделён на `research_*` и `execution_*`. Mark-to-market остаётся только исследовательским prior и не является доказательством прибыльности.
4. Profitability Kill Switch: live/Paper-кандидат требует Execution Champion, минимум execution samples, положительный robust return и robust PF выше порога. Дополнительно требуется отдельная положительная фактическая Paper-история (по умолчанию >=80 исполнений, robust PF >=1.05, robust return >0), поэтому Shadow replay не может сам по себе открыть торговый контур.
5. Убрана оптимистичная fallback-вероятность fill=70%. Если нет fill-модели, используется только эмпирический prior из реальных fill/no-fill; если нет и его — fail closed.
6. Champion Execution ML обязан быть лучше исходной Probability и base-rate по Brier, пройти AUC, untouched champion split и return/PF проверки. Dataset дополнительно проходит label-quality gate: минимум 80 filled, 40 no-fill и 120 resolved outcomes; all-filled dataset блокируется как `invalid-label-balance`.
7. Добавлен direction gate. LONG/SHORT допускаются только после достаточной execution-истории соответствующего направления.
8. BREAKOUT остаётся Shadow до достаточного числа execution-примеров, после чего должен иметь положительный specialist edge.
9. Adaptive Model: минимум 200 trades, минимум 60 validation, runtime weight cap 5%.
10. AI Optimizer: минимум 200 trades; выбор порога делается на старой части истории и отдельно подтверждается на последних 30% OOS.
11. Dynamic position sizing по умолчанию выключен; Paper leverage по умолчанию ограничен 3x до доказанного edge.
12. Execution Profit Profile считает return/R на `net_pnl/notional` после комиссий/slippage, а не на сырых движениях цены.
13. Исправлена winsorization: редкие прибыльные сделки больше не могут быть превращены в отрицательные из-за глобального 95-го перцентиля.
14. Исправлена синхронизация Learning: `HORIZON_SL/HORIZON_TP*` канонизируются в `SL/TP*`; missing JSONB поля сохраняются как `{}` для старых NOT NULL схем.
15. Background order: V57 backfill запускается раньше profile rebuild и Execution training.

## Безопасная логика запуска

После установки V57 бот может показать `NO_TRADE` для всех сигналов. Это ожидаемо, если execution edge ещё не доказан. V57 не разрешает обходить отрицательную реальную статистику красивым mark-to-market PF.

По собранным данным V56 новый профиль на текущих подтвержденных Paper execution показывает около 45 валидных закрытых исполнений, WR ~6.7%, robust PF ~0.21 и отрицательный robust return. Поэтому V57 корректно останется fail-closed до накопления и подтверждения нового положительного OOS edge.

## Критерий profit-ready

По умолчанию требуются одновременно:
- validated Execution ML champion;
- >=120 execution outcomes в profitability profile;
- >=80 фактических Paper execution;
- robust execution PF >=1.15;
- robust Paper PF >=1.05;
- robust avg net return >0;
- model AUC/Brier/OOS gates;
- направление имеет >=60 execution samples с положительным edge;
- setup-specific guard не отрицательный.

Это не обещание будущей прибыли. Это технический запрет на объявление модели прибыльной без подтверждения execution-only статистикой.
