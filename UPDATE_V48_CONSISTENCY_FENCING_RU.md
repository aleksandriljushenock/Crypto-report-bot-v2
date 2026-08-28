# V48.0.0 — Consistency, Fencing & Historical Recovery

V48 закрывает найденные в расширенном аудите V47 проблемы multi-model/multi-instance consistency.

Основное:
- model_registry promotion изолирован по model_name;
- target проверяется до деактивации champion;
- load_active_model фильтрует model_name;
- candidate сохраняется как challenger, cloud promotion выполняется до локальной активации;
- promotion может быть fenced текущим distributed training lease generation;
- lease heartbeat имеет retry и выставляет lease-lost;
- Shadow восстанавливает старое entry-window через адаптивный historical interval;
- scanner создаёт event_id/eventFingerprint для каждого отдельного события;
- Learning использует event identity вместо 6h structural bucket;
- Paper execution history умеет переходить на 1h/4h для глубокого восстановления;
- unresolved Paper lifecycle получает terminal policy и atomic void without fabricated PnL;
- V48 paper reconcile учитывает entry fee у void_data позиций.

Обязательная миграция: `migrations/SUPABASE_V48_INTEGRITY.sql` после V47.
