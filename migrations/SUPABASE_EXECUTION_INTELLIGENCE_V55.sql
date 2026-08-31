-- V55 unified execution intelligence dataset. Safe to run repeatedly.
create table if not exists public.execution_training_dataset_v55 (
  sample_id text primary key,
  source_id text,
  sample_type text not null,
  decision_at_signal text,
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
  provider_attempts jsonb not null default '[]'::jsonb,
  candle_interval text,
  ambiguous_same_candle boolean not null default false,
  label_version text not null default 'first_hit_v55',
  sample_weight double precision not null default 1.0,
  feature_payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);
create index if not exists idx_execution_v55_signal_time on public.execution_training_dataset_v55(signal_created_at);
create index if not exists idx_execution_v55_specialist on public.execution_training_dataset_v55(setup,direction,signal_created_at);
create index if not exists idx_execution_v55_type on public.execution_training_dataset_v55(sample_type,entry_status,signal_created_at);
alter table public.execution_training_dataset_v55 enable row level security;
