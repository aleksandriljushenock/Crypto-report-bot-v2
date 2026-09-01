# V56 Quality & Validation Hardening

V56 исправляет ошибки V55 в execution replay и ужесточает допуск ML-моделей в runtime.

Ключевые изменения:
- правильный close-time 5m свечей для Bybit/OKX/Bitget/Gate/MEXC;
- chunk/pagination backfill на полный 72h горизонт, включая OKX;
- retries и provider error diagnostics;
- legacy Shadow enrichment по fingerprint и fallback key;
- новый execution_training_dataset_v56; Paper labels мигрируются, Shadow переигрываются;
- train/calibration/selection/untouched champion split с 72h embargo;
- Champion требует >=2 независимо прошедших моделей и stricter OOS thresholds;
- weighted ensemble по AUC/Brier, без бонуса за modelCount;
- отдельная строгая OOS-валидация Expected Return;
- joint executable probability = P(fill) * P(profit|fill);
- безопасные unified defaults для Adaptive/Optimizer/Multi-Exchange;
- AI Optimizer ищет пороги в обе стороны и учитывает drawdown/CVaR;
- manual Profit Profile rebuild теперь тоже включает execution replay V56.

После деплоя обязательно выполнить migrations/SUPABASE_EXECUTION_INTELLIGENCE_V56.sql,
затем backfill_execution_dataset_v56.py и только после этого execution_model_v56.train().
