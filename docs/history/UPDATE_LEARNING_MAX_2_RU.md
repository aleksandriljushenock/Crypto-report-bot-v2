# Crypto Intelligence Platform — Learning MAX 2.0

## Что добавлено

- Совместимый слой `learning_max2.py` поверх существующего `learning_engine_v14.py`.
- Online/Incremental Learning через безопасное накопление наблюдений и результатов.
- Walk Forward Validation и Champion/Challenger остаются под защитными gate-механизмами v14.
- Drift Detection, probability calibration, uncertainty estimation.
- Feature Store SQLite (`data/learning_max2.db`) с WAL и индексами.
- Отдельные specialist-модели: Trend, Momentum, Breakout, Reversal, Volatility, Risk и Ensemble.
- Feature Importance и автоматический отбор существенных признаков.
- Explainable AI: причины, сильные/слабые факторы, вероятность, уверенность, причины входа/отказа.
- Smart Money Score из whale alert, exchange netflow, ETF/stablecoin flows, funding, OI, liquidations.
- News Intelligence: RSS, sentiment, impact, deduplication, topic clustering.
- GPT/API fallback: локальная детерминированная модель продолжает работу при отказе внешнего AI.
- Telegram-команды `/learnmax`, `/modelstatus`, `/smartmoney`, `/news`, `/market`, `/regime`, `/confidence`, `/features`, `/health`.

## Обновление

1. Остановить текущий сервис.
2. Сделать резервную копию каталога `data/` и файла `.env`.
3. Распаковать архив поверх отдельной новой папки.
4. Перенести свой `.env` и при необходимости существующий `data/`.
5. Создать окружение: `python -m venv .venv`.
6. Активировать окружение и выполнить `pip install -r requirements.txt`.
7. Проверить: `python healthcheck.py` и `python test_learning_max2.py`.
8. Запустить используемую точку входа (`telegram_command_bot.py`, `app.py` или ваш scheduler).

Новые таблицы создаются автоматически. Существующие базы не удаляются и не пересоздаются.
