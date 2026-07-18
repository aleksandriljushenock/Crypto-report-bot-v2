# Обновление v7: AI Trading, Watchlist и статистика

Добавлено:

- мульти-таймфрейм профиль 1D / 4H / 1H / 15M / 5M;
- Alignment Score направления;
- AI Trade Profile: Trend, Momentum, Volume, Funding, OI и Risk;
- оценка вероятности сценария и уверенности;
- дополнительный порог `TRADE_MIN_PROBABILITY`;
- автоматический AI Watchlist;
- трекинг исходов торговых сигналов через 1h, 24h и 7d;
- отчёт эффективности сигналов;
- Telegram-команды `/watchlist` и `/performance`;
- кнопки `⭐ Watchlist` и `📈 Результаты`.

## Установка

Скопируйте файлы поверх текущего проекта, не заменяя `.env` и папку `data`.

```cmd
cd C:\Users\user\crypto_report_service
.venv\Scripts\activate
pip install -r requirements.txt
python -m compileall .
python telegram_command_bot.py
```

## Рекомендуемые параметры `.env`

```env
TRADE_MIN_SCORE=72
TRADE_MIN_RR=2.0
TRADE_MIN_PROBABILITY=65
TRADE_OUTCOME_TRACKER_ENABLED=true
TRADE_OUTCOME_UPDATE_INTERVAL_MINUTES=60
```

Вероятность является модельной оценкой качества совпадения факторов, а не гарантированной статистической вероятностью прибыли. Бот не открывает сделки автоматически.
