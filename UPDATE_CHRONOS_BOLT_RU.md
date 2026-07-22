# Chronos-Bolt: предобученный эксперт временных рядов

Добавлен опциональный zero-shot прогноз Amazon Chronos-Bolt Tiny.

## Как работает

1. Основной движок сначала рассчитывает технический сигнал как раньше.
2. Chronos запускается только для сигналов, уже прошедших базовые фильтры score и RR.
3. Модель прогнозирует медианную цену, диапазон 10–90% и неопределенность.
4. Результат смешивается с исходной вероятностью с ограниченным весом до 25%.
5. При ошибке загрузки, нехватке памяти или отсутствии сети бот продолжает работать без Chronos.

Chronos не заменяет Learning Engine v14 и не активирует сделку сам. Это дополнительный эксперт.

## Переменные Render

```env
CHRONOS_ENABLED=true
CHRONOS_MODEL=amazon/chronos-bolt-tiny
CHRONOS_TIMEFRAME=15m
CHRONOS_MIN_CONTEXT=64
CHRONOS_CONTEXT_LENGTH=192
CHRONOS_PREDICTION_LENGTH=12
CHRONOS_MAX_WEIGHT=0.25
HF_HOME=/tmp/huggingface
```

На первом прогнозе модель скачивается с Hugging Face (около 35 МБ без учета Python-зависимостей), поэтому первый вызов будет медленнее.

## Ограниченная память Render

Если сервис получает OOM:

```env
CHRONOS_ENABLED=false
```

Это мгновенно возвращает старое поведение. Для стабильной работы модели предпочтительно не менее 1 ГБ RAM. Не увеличивайте `CHRONOS_MAX_WEIGHT` выше 0.30 до появления собственных walk-forward метрик.

## Проверка

В логах при первой успешной загрузке:

```text
Chronos loaded: model=amazon/chronos-bolt-tiny
```

В сигнале появится объект `chronos` с полями `forecastReturnPct`, `probabilityUp`, `uncertaintyPct`, `weight` и `directionAgreement`.
