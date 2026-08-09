-- v19: realistic Paper entry execution + invalidate the TUTUSDT phantom fill.
-- Safe to run repeatedly.
begin;

-- Allow pending entry orders in the existing paper_positions table.
alter table public.paper_positions drop constraint if exists paper_positions_status_check;
alter table public.paper_positions
    add constraint paper_positions_status_check
    check (status in ('pending_entry','open','closed','cancelled'));

alter table public.paper_positions add column if not exists signal_entry_price double precision;
alter table public.paper_positions add column if not exists entry_zone_low double precision;
alter table public.paper_positions add column if not exists entry_zone_high double precision;
alter table public.paper_positions add column if not exists trigger_price double precision;
alter table public.paper_positions add column if not exists pending_until timestamptz;
alter table public.paper_positions add column if not exists pending_reason text;
alter table public.paper_positions add column if not exists fill_price_source text;

create index if not exists paper_positions_pending_idx
    on public.paper_positions(status, pending_until)
    where status = 'pending_entry';

-- Invalidate the 2026-08-08 08:51 MSK TUTUSDT paper fill.
-- 08:51 MSK = 05:51 UTC. The window is intentionally narrow.
do $$
declare
    p record;
    t record;
    acc record;
begin
    select * into p
    from public.paper_positions
    where symbol = 'TUTUSDT'
      and created_at >= timestamptz '2026-08-08 05:45:00+00'
      and created_at <  timestamptz '2026-08-08 06:05:00+00'
    order by created_at asc
    limit 1;

    if p.id is null then
        raise notice 'TUTUSDT target paper position not found; nothing to invalidate.';
        return;
    end if;

    select * into acc from public.paper_accounts where id = p.account_id for update;
    select * into t from public.paper_trades where position_id = p.id order by closed_at desc limit 1;

    if p.status = 'open' then
        update public.paper_accounts
        set balance = balance + coalesce(p.margin_usd,0) + coalesce(p.entry_fee,0),
            equity = equity + coalesce(p.entry_fee,0),
            fees_paid = greatest(0, fees_paid - coalesce(p.entry_fee,0)),
            updated_at = now()
        where id = p.account_id;
    elsif p.status = 'closed' and t.id is not null then
        update public.paper_accounts
        set balance = balance - coalesce(t.net_pnl,0),
            equity = equity - coalesce(t.net_pnl,0),
            realized_pnl = realized_pnl - coalesce(t.net_pnl,0),
            fees_paid = greatest(0, fees_paid - coalesce(t.fees,0)),
            updated_at = now()
        where id = p.account_id;
        delete from public.paper_trades where id = t.id;
    end if;

    update public.paper_positions
    set status = 'cancelled',
        close_reason = 'INVALID_FILL_PRE_V19',
        pending_reason = 'Signal emitted while market was above pullback entry; phantom midpoint fill invalidated',
        gross_pnl = null,
        net_pnl = null,
        exit_price = null,
        closed_at = now(),
        updated_at = now()
    where id = p.id;

    raise notice 'TUTUSDT paper position % invalidated and account accounting reversed.', p.id;
end $$;

comment on column public.paper_positions.signal_entry_price is 'Calculated desired entry from the signal; not an execution until trigger/touch.';
comment on column public.paper_positions.pending_until is 'Deadline after which an unfilled paper order is cancelled as ENTRY_EXPIRED.';
comment on column public.paper_positions.fill_price_source is 'How the simulated fill was obtained (limit touch, breakout market, candle trigger).';

notify pgrst, 'reload schema';
commit;
