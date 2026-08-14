-- v36: durable Strategy Lab statistics.
-- Safe to run more than once. Does not delete strategy_setups history.

create table if not exists public.strategy_statistics (
  strategy text primary key,
  total integer not null default 0,
  resolved integer not null default 0,
  wins integer not null default 0,
  losses integer not null default 0,
  breakeven integer not null default 0,
  waiting integer not null default 0,
  open integer not null default 0,
  expired integer not null default 0,
  win_rate double precision not null default 0,
  avg_return double precision not null default 0,
  profit_factor double precision not null default 0,
  expectancy double precision not null default 0,
  cumulative_return double precision not null default 0,
  compounded_return double precision not null default 0,
  max_drawdown double precision not null default 0,
  gross_win double precision not null default 0,
  gross_loss double precision not null default 0,
  first_setup_at timestamptz,
  last_setup_at timestamptz,
  updated_at timestamptz not null default now()
);

create table if not exists public.strategy_stats_daily (
  strategy text not null,
  stat_date date not null,
  metrics jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  primary key (strategy, stat_date)
);

create index if not exists strategy_stats_daily_date_idx
  on public.strategy_stats_daily(stat_date desc, strategy);

alter table public.strategy_statistics enable row level security;
alter table public.strategy_stats_daily enable row level security;

notify pgrst, 'reload schema';
