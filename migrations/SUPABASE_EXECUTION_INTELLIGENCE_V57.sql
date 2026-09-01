-- V57 profitability-first execution truth. Safe to run repeatedly.
create table if not exists public.execution_training_dataset_v57 (
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
  label_version text not null default 'first_hit_v57',
  sample_weight double precision not null default 1.0,
  feature_payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  constraint execution_v57_entry_status_check check (entry_status in ('filled','no_fill','unresolved'))
);
create index if not exists idx_execution_v57_signal_time on public.execution_training_dataset_v57(signal_created_at);
create index if not exists idx_execution_v57_specialist on public.execution_training_dataset_v57(setup,direction,signal_created_at);
create index if not exists idx_execution_v57_type on public.execution_training_dataset_v57(sample_type,entry_status,signal_created_at);
alter table public.execution_training_dataset_v57 enable row level security;

-- Do NOT copy V56 Paper labels: V56 could mark cancelled orders as filled.
-- V57 must be rebuilt by backfill_execution_dataset_v57.py from canonical Paper/Shadow sources.

-- Profit-first defaults. Existing values are tightened only where they are known old defaults.
update public.strategy_settings set value='200', updated_at=now(), updated_by='migration_v57'
 where key='ADAPTIVE_MODEL_MIN_TRADES' and value::numeric < 200;
update public.strategy_settings set value='60', updated_at=now(), updated_by='migration_v57'
 where key='ADAPTIVE_MODEL_MIN_VALIDATION' and value::numeric < 60;
update public.strategy_settings set value='0.05', updated_at=now(), updated_by='migration_v57'
 where key='ADAPTIVE_MODEL_BLEND_WEIGHT' and value::numeric > 0.05;
update public.strategy_settings set value='200', updated_at=now(), updated_by='migration_v57'
 where key='AI_OPTIMIZER_MIN_TRADES' and value::numeric < 200;

update public.strategy_settings set value='3', updated_at=now(), updated_by='migration_v57'
 where key='PAPER_MAX_LEVERAGE' and value::numeric > 3;
update public.strategy_settings set value='false', updated_at=now(), updated_by='migration_v57'
 where key='POSITION_SIZING_ENABLED' and lower(value) in ('true','1','yes','on');

-- Register new fail-closed profitability gates when missing.
insert into public.strategy_settings (key,value,value_type,category,title,description,min_value,max_value,is_editable,updated_at,updated_by) values
 ('PROFITABILITY_MIN_PAPER_EXECUTIONS','80','int','filters','Мин. Paper execution','Минимум фактически исполненных Paper-сделок перед profit-ready режимом.',20,10000,true,now(),'migration_v57'),
 ('PROFITABILITY_MIN_PAPER_PF','1.05','float','filters','Мин. Paper PF','Минимальный robust PF на фактических Paper execution.',0,10,true,now(),'migration_v57')
on conflict (key) do nothing;

notify pgrst, 'reload schema';

-- Learning-store compatibility repair observed in v56 production logs.
update public.learning_observations set features='{}'::jsonb where features is null;
update public.learning_observations set smart_money_data='{}'::jsonb where smart_money_data is null;
update public.learning_observations set news_data='{}'::jsonb where news_data is null;
update public.learning_observations set metadata='{}'::jsonb where metadata is null;
alter table public.learning_observations alter column features set default '{}'::jsonb;
alter table public.learning_observations alter column smart_money_data set default '{}'::jsonb;
alter table public.learning_observations alter column news_data set default '{}'::jsonb;
alter table public.learning_observations alter column metadata set default '{}'::jsonb;
