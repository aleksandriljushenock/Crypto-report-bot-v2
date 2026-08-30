-- V54 real execution replay dataset. Safe to run repeatedly.
create table if not exists public.execution_training_dataset_v54 (
  shadow_id text primary key,
  fingerprint text,
  symbol text not null,
  direction text,
  setup text,
  source text,
  signal_created_at timestamptz not null,
  entry_status text not null,
  target_entry double precision,
  actual_entry double precision,
  filled_at timestamptz,
  exit_at timestamptz,
  exit_reason text,
  outcome text,
  net_return_pct double precision,
  r_multiple double precision,
  mfe_pct double precision,
  mae_pct double precision,
  bars_to_exit integer,
  fill_delay_minutes double precision,
  provider text,
  candle_interval text,
  label_version text not null default 'first_hit_v54',
  feature_payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);
create index if not exists idx_execution_v54_signal_time on public.execution_training_dataset_v54(signal_created_at);
create index if not exists idx_execution_v54_specialist on public.execution_training_dataset_v54(setup,direction,signal_created_at);
create index if not exists idx_execution_v54_entry_status on public.execution_training_dataset_v54(entry_status,signal_created_at);
alter table public.execution_training_dataset_v54 enable row level security;
