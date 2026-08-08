# Новости только по запросу

В этой версии новости продолжают собираться фоновым сервисом и сохраняться в AI snapshots. Они по-прежнему участвуют в расчётах факторов `news`, Learning MAX и AI Score.

Из обычного отображения удалены:

- автоматические Telegram-уведомления о важных новостях;
- секция новостей из полного Professional Report;
- карточка новостей из веб-панели Dashboard.

Новости остаются доступны только по явному запросу пользователя:

- кнопка `📰 Новости рынка`;
- команда `/news`.

Рекомендуемые переменные Render:

```env
NEWS_ENGINE_ENABLED=true
NEWS_AUTO_NOTIFICATIONS=false
NEWS_INTERVAL_MINUTES=10
NEWS_NOTIFY_MIN_IMPACT=75
```

`NEWS_ENGINE_ENABLED=true` обязательно оставляет сбор новостей для расчётов. Переменная `NEWS_AUTO_NOTIFICATIONS=false` запрещает самопроизвольную отправку новостей в Telegram.
