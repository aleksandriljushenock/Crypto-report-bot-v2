# V55 Execution Quality

V55 закрывает интеграционные проблемы V54 и делает execution dataset единым источником данных для ML и health/profile logic.

## Что изменено

- Полный signal-time snapshot сохраняется в Shadow вместо урезанного dict.
- Для старых Shadow rows backfill пытается восстановить богатые features по fingerprint из `learning_observations`.
- Новая таблица `execution_training_dataset_v55` объединяет Shadow и Paper, сохраняя тип и вес примера.
- Paper execution имеет повышенный sample weight и полный `signal_payload`.
- Profit Profile читает V55 replay dataset; Shadow execution теперь реально влияет на execution health и снимает V54 recovery deadlock.
- P(fill) никогда не падает обратно к 100%: fallback = specialist/global empirical Bayesian fill prior.
- Expected Return допускается в runtime только после собственной OOS проверки MAE, Spearman и sign accuracy.
- Train/calibration/test разделены 72h purge/embargo.
- Ансамбль расширен независимыми HGB/ExtraTrees/RandomForest, rolling windows и raw/all feature sets.
- Base/raw models не используют старые Score/Probability/Quality/EV; meta models могут использовать их отдельно.
- Same-candle TP/SL сначала уточняется на 1m; неразрешённая неоднозначность получает `AMBIGUOUS` и исключается из outcome training.
- Backfill использует provider fallback Binance → Bybit → OKX → Bitget → Gate → MEXC вместо жёсткой привязки к Binance.
- Сохраняются `provider_attempts`, `decision_at_signal`, `sample_type`, missingness и feature coverage.
- В режиме SEVERE по умолчанию live закрыт; только доказавший OOS преимущество V55 champion может получить маленький deterministic canary для восстановления.

## Миграция

В Supabase SQL Editor выполнить:

`migrations/SUPABASE_EXECUTION_INTELLIGENCE_V55.sql`

## Backfill

```bash
python backfill_execution_dataset_v55.py --limit 20000
```

Команда одновременно:
1. переносит Paper positions в unified dataset;
2. replay'ит Shadow fills;
3. пытается восстановить legacy Shadow feature payload;
4. уточняет ambiguous 5m bars на 1m;
5. сохраняет unresolved/ambiguous без выдуманного label.

## Обучение

```bash
python -c "from execution_model_v55 import train; print(train(trigger='manual'))"
```

`status=shadow` является штатным результатом: модель не допускается в runtime без OOS superiority.

## Мощный сервер

Используй `.env.v55.high_capacity.example` как набор override-параметров. Не заменяй им секреты или весь production `.env`.
