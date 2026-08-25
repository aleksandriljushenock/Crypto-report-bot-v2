# V46.0.0 — Event-time & Recovery Integrity

V46 закрывает оставшиеся проблемы аудита V45.

- Shadow entry учитывает только завершённые 5m свечи внутри TTL; вход после expires_at запрещён.
- При неполной истории Shadow не ставит ложный expired, а помечает entry_unresolved.
- Shadow fingerprint теперь event-specific, повторяющиеся сетапы не блокируются навсегда.
- Shadow cloud recovery пагинируется, cloud errors логируются.
- Outcome dedupe учитывает setup и entry, observed_at соответствует target time.
- Невосстанавливаемая история получает terminal marker outcome_failures.
- Paper boundary ambiguity и TIME_EXIT без легальной свечи становятся execution_unresolved, а не ложным breakeven/expired.
- Cloud observation RPC при неоднозначном timeout работает fail-closed и не делает blind INSERT.
- V46 migration сохраняет наиболее полную fingerprint observation при dedupe.
- Добавлен distributed Supabase training lease для V14 между разными VPS/контейнерами; локальный flock сохранён.
- Recovery pending default увеличен до 10000.
- LOW_MEMORY_MODE=false в обоих production examples.

Обязательная миграция: migrations/SUPABASE_V46_INTEGRITY.sql (после V45).
