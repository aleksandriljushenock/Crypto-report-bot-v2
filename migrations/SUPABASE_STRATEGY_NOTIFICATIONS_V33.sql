-- v33: durable Strategy Lab Telegram notification state.
-- Safe to run more than once.
alter table public.strategy_setups
  add column if not exists ready_notified_at timestamptz,
  add column if not exists open_notified_at timestamptz,
  add column if not exists close_notified_at timestamptz;

create index if not exists strategy_setups_ready_notify_idx
  on public.strategy_setups(state, created_at)
  where ready_notified_at is null;

create index if not exists strategy_setups_open_notify_idx
  on public.strategy_setups(state, entered_at)
  where open_notified_at is null;

create index if not exists strategy_setups_close_notify_idx
  on public.strategy_setups(state, resolved_at)
  where close_notified_at is null;

notify pgrst, 'reload schema';
