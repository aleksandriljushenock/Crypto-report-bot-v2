-- V56 execution quality hardening. Safe to run repeatedly.
create table if not exists public.execution_training_dataset_v56 (
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
  label_version text not null default 'first_hit_v56',
  sample_weight double precision not null default 1.0,
  feature_payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);
create index if not exists idx_execution_v56_signal_time on public.execution_training_dataset_v56(signal_created_at);
create index if not exists idx_execution_v56_specialist on public.execution_training_dataset_v56(setup,direction,signal_created_at);
create index if not exists idx_execution_v56_type on public.execution_training_dataset_v56(sample_type,entry_status,signal_created_at);
alter table public.execution_training_dataset_v56 enable row level security;

-- Paper labels are canonical and may be carried forward. Shadow labels must be replayed again
-- because V56 fixes non-Binance candle-end timestamps and 72h pagination.
insert into public.execution_training_dataset_v56 (
  sample_id,source_id,sample_type,decision_at_signal,fingerprint,symbol,direction,setup,source,
  signal_created_at,entry_status,target_entry,actual_entry,filled_at,exit_at,exit_reason,outcome,
  net_return_pct,r_multiple,mfe_pct,mae_pct,bars_to_exit,fill_delay_minutes,provider,provider_attempts,
  candle_interval,ambiguous_same_candle,label_version,sample_weight,feature_payload,updated_at
)
select sample_id,source_id,sample_type,decision_at_signal,fingerprint,symbol,direction,setup,source,
  signal_created_at,entry_status,target_entry,actual_entry,filled_at,exit_at,exit_reason,outcome,
  net_return_pct,r_multiple,mfe_pct,mae_pct,bars_to_exit,fill_delay_minutes,provider,provider_attempts,
  candle_interval,ambiguous_same_candle,'paper_verified_v56',sample_weight,feature_payload,now()
from public.execution_training_dataset_v55
where sample_type like 'PAPER%'
on conflict (sample_id) do nothing;

-- Upgrade only known legacy defaults. Explicit user-tuned values are left untouched.
update public.strategy_settings set value='150', updated_at=now(), updated_by='migration_v56'
 where key='ADAPTIVE_MODEL_MIN_TRADES' and value in ('40','20');
update public.strategy_settings set value='30', updated_at=now(), updated_by='migration_v56'
 where key='ADAPTIVE_MODEL_MIN_VALIDATION' and value in ('12','8');
update public.strategy_settings set value='0.1', updated_at=now(), updated_by='migration_v56'
 where key='ADAPTIVE_MODEL_BLEND_WEIGHT' and value in ('0.2','0.20');
update public.strategy_settings set value='150', updated_at=now(), updated_by='migration_v56'
 where key='AI_OPTIMIZER_MIN_TRADES' and value in ('20','40');
update public.strategy_settings set value='2', updated_at=now(), updated_by='migration_v56'
 where key='MULTI_EXCHANGE_MIN_VENUES' and value='1';
update public.strategy_settings set value='3', updated_at=now(), updated_by='migration_v56'
 where key='RULE_WEIGHT_PROBABILITY_TREND_LIQUIDITY' and value='10';
update public.strategy_settings set value='2', updated_at=now(), updated_by='migration_v56'
 where key='RULE_WEIGHT_DAILY_MICRO_ALIGNMENT' and value='8';
update public.strategy_settings set value='1.5', updated_at=now(), updated_by='migration_v56'
 where key='RULE_WEIGHT_FLOW_ALIGNMENT_VOLUME' and value='5';

notify pgrst, 'reload schema';
