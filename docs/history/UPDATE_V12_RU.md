# Crypto Intelligence Platform v12

## Что добавлено

- Единый объяснимый AI Score 0–100.
- 11 факторов: trend, momentum, volume, funding, OI, alignment, risk/reward, capital flow, narrative, news, smart money.
- TOP AI рейтинг и история оценок.
- Автоматические AI Alerts с порогом из `.env`.
- Хранение истории в `data/ai_intelligence.db`.
- Консервативное адаптивное обучение весов по 24h outcome.
- Dashboard 2.0 с рейтингом и API.
- Команды Telegram: `/topai`, `/aihistory BTCUSDT`.

## Обновление

Сохраните `.env` и папку `data`, распакуйте архив поверх проекта, затем:

```cmd
.venv\Scripts\activate
pip install -r requirements.txt
python -m compileall .
python healthcheck.py
python telegram_command_bot.py
```

Для Dashboard:

```cmd
run_dashboard.bat
```

## Важно

AI Score — аналитическая оценка, а не гарантия прибыли. Адаптивные веса начинают реально меняться после накопления достаточной выборки 24-часовых исходов.
