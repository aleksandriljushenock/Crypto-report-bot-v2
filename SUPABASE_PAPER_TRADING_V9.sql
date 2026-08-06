-- Paper trading ledger for signal-driven simulation. Safe to run repeatedly.
create extension if not exists pgcrypto;

create table if not exists public.paper_accounts (
    id text primary key,
    initial_balance double precision not null default 100,
    balance double precision not null default 100,
    equity double precision not null default 100,
    realized_pnl double precision not null default 0,
    fees_paid double precision not null default 0,
    status text not null default 'active' check (status in ('active','paused','unavailable')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.paper_positions (
    id uuid primary key default gen_random_uuid(),
    account_id text not null references public.paper_accounts(id) on delete cascade,
    fingerprint text not null unique,
    symbol text not null,
    side text not null check (side in ('LONG','SHORT')),
    status text not null default 'open' check (status in ('open','closed','cancelled')),
    source text,
    entry_price double precision not null,
    stop_price double precision not null,
    tp1_price double precision not null,
    tp2_price double precision,
    tp3_price double precision,
    exit_price double precision,
    margin_usd double precision not null,
    leverage integer not null,
    notional_usd double precision not null,
    quantity double precision not null,
    estimated_liquidation_price double precision,
    stop_distance_pct double precision,
    liquidation_buffer_pct double precision,
    entry_fee double precision not null default 0,
    gross_pnl double precision,
    net_pnl double precision,
    close_reason text,
    quality_score double precision,
    probability double precision,
    expected_value_pct double precision,
    strategy_version text,
    signal_payload jsonb not null default '{}'::jsonb,
    opened_at timestamptz not null default now(),
    last_checked_at timestamptz not null default now(),
    max_hold_until timestamptz,
    closed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.paper_trades (
    id uuid primary key default gen_random_uuid(),
    account_id text not null references public.paper_accounts(id) on delete cascade,
    position_id uuid references public.paper_positions(id) on delete set null,
    fingerprint text not null,
    symbol text not null,
    side text not null,
    entry_price double precision not null,
    exit_price double precision not null,
    stop_price double precision,
    target_price double precision,
    margin_usd double precision not null,
    leverage integer not null,
    notional_usd double precision not null,
    gross_pnl double precision not null,
    net_pnl double precision not null,
    return_on_margin_pct double precision not null,
    fees double precision not null default 0,
    close_reason text not null,
    quality_score double precision,
    probability double precision,
    expected_value_pct double precision,
    strategy_version text,
    opened_at timestamptz,
    closed_at timestamptz not null default now(),
    created_at timestamptz not null default now()
);

create index if not exists paper_positions_status_idx on public.paper_positions(status, opened_at);
create index if not exists paper_positions_symbol_idx on public.paper_positions(symbol, status);
create index if not exists paper_trades_closed_idx on public.paper_trades(closed_at desc);
create index if not exists paper_trades_symbol_idx on public.paper_trades(symbol, closed_at desc);

alter table public.paper_accounts enable row level security;
alter table public.paper_positions enable row level security;
alter table public.paper_trades enable row level security;

-- No public policies: server bot uses SUPABASE_SERVICE_KEY.
comment on table public.paper_accounts is 'Paper trading account balance and cumulative PnL.';
comment on table public.paper_positions is 'Open and closed simulated positions created from final bot signals.';
comment on table public.paper_trades is 'Immutable paper trading close ledger.';

notify pgrst, 'reload schema';
