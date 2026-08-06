# Chronos Memory-Safe v2

Chronos перенесён в финальную стадию отбора. Сначала дешёвые правила, Learning MAX и Hedge Engine ранжируют кандидатов, затем Chronos запускается только для `CHRONOS_FINALISTS` лучших сигналов.

## Защита памяти

- модель загружается лениво;
- до загрузки проверяется текущий RSS процесса;
- после финального пакета модель удаляется и вызываются `gc.collect()` и `malloc_trim()`;
- контекст сокращён до 128 точек, горизонт до 8;
- по умолчанию анализируется один финалист;
- при нехватке запаса памяти Chronos пропускается, а основной сигнал продолжает работать;
- поддержан удалённый режим через `CHRONOS_MODE=remote`.

## Render ENV для 512 MB

```env
CHRONOS_ENABLED=true
CHRONOS_MODE=local
CHRONOS_MODEL=amazon/chronos-bolt-tiny
CHRONOS_FINALISTS=1
CHRONOS_CONTEXT_LENGTH=128
CHRONOS_PREDICTION_LENGTH=8
CHRONOS_MAX_WEIGHT=0.18
CHRONOS_UNLOAD_AFTER_BATCH=true
CHRONOS_MEMORY_GUARD_ENABLED=true
CHRONOS_REQUIRED_HEADROOM_MB=230
MEMORY_SOFT_LIMIT_MB=390
MEMORY_HARD_LIMIT_MB=500
WEB_CONCURRENCY=1
GUNICORN_WORKERS=1
GUNICORN_THREADS=1
OMP_NUM_THREADS=1
MKL_NUM_THREADS=1
OPENBLAS_NUM_THREADS=1
NUMEXPR_NUM_THREADS=1
```

Если локальная модель всё равно вызывает status 137, это означает, что базовый процесс плюс PyTorch не помещаются в лимит. Тогда Chronos нужно вынести в отдельный сервис и настроить `CHRONOS_MODE=remote`.

## Supabase

Повторно выполните `SUPABASE_AI_HEDGE_FUND_V1.sql`. Он добавляет поля `chronos_probability`, `chronos_return_pct`, `chronos_agreement`, `chronos_model`, `chronos_status` и обновляет dashboard view.
