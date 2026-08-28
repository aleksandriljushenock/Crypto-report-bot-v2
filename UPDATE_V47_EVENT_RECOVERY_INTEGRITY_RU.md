# V47.0.0 — Event & Recovery Integrity

V47 закрывает дефекты, найденные шестипроходным аудитом V46.

Основное:
- Paper использует event fingerprint: одинаковая структура сигнала может торговаться снова в новом событии, а повтор одного события дедуплицируется.
- Paper не использует незакрытые execution candles для entry/TP/SL.
- Неоднозначные boundary/timeout случаи остаются в pending/open lifecycle и повторно проверяются; margin не замораживается в недоступном статусе.
- Paper tracker возвращает degraded/error при market/storage errors вместо ложного ok.
- Shadow использует event-specific fingerprint, не открывается после TTL, игнорирует незакрытые свечи, повторно восстанавливает entry_unresolved и становится observed только после всех 6h/12h/24h outcomes.
- Shadow snapshot labels явно помечены HORIZON_* и не выдаются за path-dependent execution TP/SL.
- Learning observation без достоверной market baseline не создаётся.
- unrecoverable-history сохраняется локально и best-effort в cloud metadata; terminal rows участвуют в retention cleanup.
- V14 calibration строится отдельно для regime:LONG / regime:SHORT и runtime выбирает directional calibration.
- Cloud V14 promotion выполняется атомарной RPC model_registry_promote_v47; добавлен unique active index на model_name.
- Distributed V14 lease получил heartbeat/renew, release retry и distributed training_running status.
- Ручная активация V14 также использует atomic cloud promotion.

Обязательная миграция после V46:
`migrations/SUPABASE_V47_INTEGRITY.sql`
