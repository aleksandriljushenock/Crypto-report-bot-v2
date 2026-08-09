-- V22 shadow-signal persistence. Safe to run more than once.
create table if not exists public.shadow_signals_v22 (
  id text primary key,
  symbol text not null,
  direction text,
  setup text,
  reason text,
  source text,
  created_at timestamptz not null,
  expires_at timestamptz not null,
  status text not null default 'pending_entry',
  target_entry double precision,
  actual_entry double precision,
  filled_at timestamptz,
  stop double precision,
  tp1 double precision,
  tp2 double precision,
  tp3 double precision,
  score double precision,
  probability double precision,
  quality double precision,
  ev double precision,
  payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create index if not exists idx_shadow_signals_v22_status on public.shadow_signals_v22(status, created_at desc);
create index if not exists idx_shadow_signals_v22_symbol on public.shadow_signals_v22(symbol, created_at desc);

create table if not exists public.shadow_outcomes_v22 (
  shadow_id text not null references public.shadow_signals_v22(id) on delete cascade,
  horizon_hours integer not null,
  observed_at timestamptz not null,
  price double precision,
  return_pct double precision,
  label text,
  primary key (shadow_id, horizon_hours)
);

alter table public.shadow_signals_v22 enable row level security;
alter table public.shadow_outcomes_v22 enable row level security;
-- Backend uses SUPABASE_SERVICE_KEY, which bypasses RLS. No public policies are created.
