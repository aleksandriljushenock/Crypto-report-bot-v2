-- V38: atomic Paper order lifecycle + PostgREST-compatible ledger idempotency.
-- Safe to run repeatedly.
begin;

alter table public.paper_positions add column if not exists execution_provider text;

-- ON CONFLICT(position_id) requires an unconditional UNIQUE target. PostgreSQL
-- UNIQUE permits multiple NULL values, so the old partial predicate is unnecessary.
drop index if exists public.paper_trades_position_id_uidx;
create unique index if not exists paper_trades_position_id_uidx
    on public.paper_trades(position_id);

-- Preserve legitimate legacy closed trades that predate fill_price_source.
update public.paper_positions
set fill_price_source = 'legacy_migrated_v38'
where status = 'closed'
  and fill_price_source is null
  and coalesce(entry_price, 0) > 0
  and coalesce(margin_usd, 0) > 0
  and opened_at is not null
  and closed_at is not null
  and upper(coalesce(close_reason, '')) not like 'INVALID_FILL%';

create or replace function public.paper_create_pending_v38(
    p_row jsonb,
    p_max_active integer,
    p_one_per_symbol boolean
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_account_id text := coalesce(p_row->>'account_id', 'main');
    v_symbol text := upper(coalesce(p_row->>'symbol', ''));
    v_fingerprint text := coalesce(p_row->>'fingerprint', '');
    v_active integer;
    v_position public.paper_positions;
begin
    perform 1 from public.paper_accounts where id = v_account_id for update;
    if not found then
        raise exception 'paper account % unavailable', v_account_id;
    end if;

    if exists(select 1 from public.paper_positions where fingerprint = v_fingerprint) then
        return null;
    end if;

    select count(*) into v_active
    from public.paper_positions
    where account_id = v_account_id and status in ('pending_entry','open');
    if v_active >= greatest(1, p_max_active) then
        return null;
    end if;

    if p_one_per_symbol and exists(
        select 1 from public.paper_positions
        where account_id = v_account_id and symbol = v_symbol and status in ('pending_entry','open')
    ) then
        return null;
    end if;

    insert into public.paper_positions (
        account_id,fingerprint,symbol,side,status,source,entry_price,stop_price,tp1_price,tp2_price,tp3_price,
        margin_usd,leverage,notional_usd,quantity,entry_fee,quality_score,probability,expected_value_pct,
        strategy_version,signal_payload,signal_entry_price,entry_zone_low,entry_zone_high,trigger_price,
        pending_until,pending_reason,opened_at,last_checked_at,created_at,updated_at,execution_audit
    ) values (
        v_account_id,v_fingerprint,v_symbol,p_row->>'side','pending_entry',p_row->>'source',
        (p_row->>'entry_price')::double precision,(p_row->>'stop_price')::double precision,(p_row->>'tp1_price')::double precision,
        nullif(p_row->>'tp2_price','')::double precision,nullif(p_row->>'tp3_price','')::double precision,
        0,1,0,0,0,nullif(p_row->>'quality_score','')::double precision,nullif(p_row->>'probability','')::double precision,
        nullif(p_row->>'expected_value_pct','')::double precision,p_row->>'strategy_version',coalesce(p_row->'signal_payload','{}'::jsonb),
        nullif(p_row->>'signal_entry_price','')::double precision,nullif(p_row->>'entry_zone_low','')::double precision,
        nullif(p_row->>'entry_zone_high','')::double precision,nullif(p_row->>'trigger_price','')::double precision,
        (p_row->>'pending_until')::timestamptz,p_row->>'pending_reason',(p_row->>'opened_at')::timestamptz,
        (p_row->>'last_checked_at')::timestamptz,(p_row->>'created_at')::timestamptz,(p_row->>'updated_at')::timestamptz,
        coalesce(p_row->'execution_audit','{}'::jsonb)
    ) returning * into v_position;
    return to_jsonb(v_position);
end $$;

create or replace function public.paper_fill_pending_v38(
    p_position_id uuid,
    p_fill_price double precision,
    p_leverage integer,
    p_liquidation double precision,
    p_stop_distance_pct double precision,
    p_liquidation_buffer_pct double precision,
    p_requested_margin double precision,
    p_reserve double precision,
    p_fee_rate double precision,
    p_fill_source text,
    p_filled_at timestamptz,
    p_max_hold_hours integer,
    p_execution_provider text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_position public.paper_positions;
    v_account public.paper_accounts;
    v_margin double precision;
    v_notional double precision;
    v_entry_fee double precision;
    v_quantity double precision;
    v_max_margin double precision;
begin
    select * into v_position from public.paper_positions where id = p_position_id for update;
    if not found or v_position.status <> 'pending_entry' then return null; end if;
    if v_position.pending_until is not null and p_filled_at >= v_position.pending_until then return null; end if;

    select * into v_account from public.paper_accounts where id = v_position.account_id for update;
    if not found or v_account.status <> 'active' then return null; end if;

    v_max_margin := greatest(0, (v_account.balance - greatest(0,p_reserve)) / (1 + greatest(1,p_leverage) * greatest(0,p_fee_rate)));
    v_margin := least(greatest(0,p_requested_margin), v_max_margin);
    if v_margin <= 0 then return null; end if;
    v_notional := v_margin * greatest(1,p_leverage);
    v_entry_fee := v_notional * greatest(0,p_fee_rate);
    v_quantity := v_notional / p_fill_price;

    update public.paper_positions set
        status='open', entry_price=p_fill_price, margin_usd=v_margin, leverage=greatest(1,p_leverage),
        notional_usd=v_notional, quantity=v_quantity, estimated_liquidation_price=p_liquidation,
        stop_distance_pct=p_stop_distance_pct, liquidation_buffer_pct=p_liquidation_buffer_pct,
        entry_fee=v_entry_fee, fill_price_source=p_fill_source, execution_provider=p_execution_provider,
        opened_at=p_filled_at, last_checked_at=p_filled_at,
        max_hold_until=p_filled_at + make_interval(hours => greatest(1,p_max_hold_hours)), updated_at=p_filled_at,
        execution_audit=coalesce(v_position.execution_audit,'{}'::jsonb) || jsonb_build_object(
            'actual_fill',p_fill_price,'fill_source',p_fill_source,'filled_at',p_filled_at,'execution_provider',p_execution_provider)
    where id=p_position_id and status='pending_entry'
    returning * into v_position;

    update public.paper_accounts set
        balance=balance-v_margin-v_entry_fee,
        equity=equity-v_entry_fee,
        fees_paid=fees_paid+v_entry_fee,
        updated_at=p_filled_at
    where id=v_position.account_id;

    return to_jsonb(v_position);
end $$;

grant execute on function public.paper_create_pending_v38(jsonb,integer,boolean) to service_role;
grant execute on function public.paper_fill_pending_v38(uuid,double precision,integer,double precision,double precision,double precision,double precision,double precision,double precision,text,timestamptz,integer,text) to service_role;

notify pgrst, 'reload schema';
commit;
