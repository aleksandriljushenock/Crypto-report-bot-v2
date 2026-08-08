# Scanner Redeploy State v16

Исправляет вводящее в заблуждение `Проверено: 0 монет` сразу после redeploy.

## Что изменено

- Если ручной скан попал в момент, когда фоновый Trade Monitor уже выполняет scan, Telegram теперь пишет `Скан уже выполняется`, а не `Проверено: 0`.
- Последний успешный Scanner Intelligence snapshot сохраняется в Supabase.
- После redeploy экран Scanner Intelligence сразу показывает последний успешный scan и помечает его как восстановленный из облака.
- История воронки за 24 часа также хранится в Supabase и не обнуляется после restart контейнера.
- Во время первого нового цикла отображается статус инициализации/выполняющегося скана.

## Supabase

Один раз выполнить `SUPABASE_SCANNER_STATE_V16.sql` в SQL Editor.

## Railway ENV

Новая обязательная переменная не нужна. По умолчанию облачное состояние включено при наличии `SUPABASE_URL` и `SUPABASE_SERVICE_KEY`.

Опционально:

```env
SCANNER_STATE_CLOUD_ENABLED=true
SCANNER_INTELLIGENCE_HISTORY_LIMIT=300
```

При `SCANNER_STATE_CLOUD_ENABLED=false` остаётся только локальный JSON fallback.
