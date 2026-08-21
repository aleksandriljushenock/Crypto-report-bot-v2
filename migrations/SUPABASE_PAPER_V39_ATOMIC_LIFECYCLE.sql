-- V39: secure and atomic Paper lifecycle. Safe to run repeatedly.
begin;

-- V38 SECURITY DEFINER functions must never be callable through anon/authenticated RPC.
revoke all on function public.paper_create_pending_v38(jsonb,integer,boolean) from public, anon, authenticated;
revoke all on function public.paper_fill_pending_v38(uuid,double precision,integer,double precision,double precision,double precision,double precision,double precision,double precision,text,timestamptz,integer,text) from public, anon, authenticated;
grant execute on function public.paper_create_pending_v38(jsonb,integer,boolean) to service_role;
grant execute on function public.paper_fill_pending_v38(uuid,double precision,integer,double precision,double precision,double precision,double precision,double precision,double precision,text,timestamptz,integer,text) to service_role;

-- Invalid historical rows are quarantined instead of being silently promoted as valid fills.
alter table public.paper_positions add column if not exists execution_verified boolean not null default false;
update public.paper_positions
set execution_verified = true
where fill_price_source is not null
  and fill_price_source <> 'legacy_migrated_v38'
  and opened_at is not null
  and coalesce(entry_price,0) > 0
  and coalesce(margin_usd,0) > 0;

create or replace function public.paper_close_v39(
    p_position_id uuid,
    p_exit_price double precision,
    p_reason text,
    p_gross_pnl double precision,
    p_net_pnl double precision,
    p_exit_fee double precision,
    p_released double precision,
    p_equity_delta double precision,
    p_closed_at timestamptz,
    p_execution_audit jsonb,
    p_trade jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_position public.paper_positions;
    v_account public.paper_accounts;
begin
    select * into v_position from public.paper_positions where id=p_position_id for update;
    if not found or v_position.status <> 'open' then return null; end if;

    select * into v_account from public.paper_accounts where id=v_position.account_id for update;
    if not found or v_account.status <> 'active' then return null; end if;

    update public.paper_positions set
        status='closed', exit_price=p_exit_price, close_reason=p_reason,
        gross_pnl=p_gross_pnl, net_pnl=p_net_pnl, closed_at=p_closed_at,
        last_checked_at=p_closed_at, updated_at=p_closed_at,
        execution_audit=coalesce(p_execution_audit,'{}'::jsonb), execution_verified=true
    where id=p_position_id and status='open'
    returning * into v_position;
    if not found then return null; end if;

    insert into public.paper_trades(
        account_id,position_id,fingerprint,symbol,side,entry_price,exit_price,stop_price,target_price,
        margin_usd,leverage,notional_usd,gross_pnl,net_pnl,return_on_margin_pct,fees,close_reason,
        quality_score,probability,expected_value_pct,strategy_version,opened_at,closed_at,created_at
    ) values (
        p_trade->>'account_id',(p_trade->>'position_id')::uuid,p_trade->>'fingerprint',p_trade->>'symbol',p_trade->>'side',
        (p_trade->>'entry_price')::double precision,(p_trade->>'exit_price')::double precision,
        nullif(p_trade->>'stop_price','')::double precision,nullif(p_trade->>'target_price','')::double precision,
        (p_trade->>'margin_usd')::double precision,(p_trade->>'leverage')::integer,(p_trade->>'notional_usd')::double precision,
        (p_trade->>'gross_pnl')::double precision,(p_trade->>'net_pnl')::double precision,
        (p_trade->>'return_on_margin_pct')::double precision,(p_trade->>'fees')::double precision,p_trade->>'close_reason',
        nullif(p_trade->>'quality_score','')::double precision,nullif(p_trade->>'probability','')::double precision,
        nullif(p_trade->>'expected_value_pct','')::double precision,p_trade->>'strategy_version',
        nullif(p_trade->>'opened_at','')::timestamptz,(p_trade->>'closed_at')::timestamptz,(p_trade->>'created_at')::timestamptz
    ) on conflict(position_id) do update set
        exit_price=excluded.exit_price,gross_pnl=excluded.gross_pnl,net_pnl=excluded.net_pnl,
        return_on_margin_pct=excluded.return_on_margin_pct,fees=excluded.fees,close_reason=excluded.close_reason,
        closed_at=excluded.closed_at;

    update public.paper_accounts set
        balance=balance+p_released,
        equity=equity+p_equity_delta,
        realized_pnl=realized_pnl+p_net_pnl,
        fees_paid=fees_paid+p_exit_fee,
        updated_at=p_closed_at
    where id=v_position.account_id;

    return jsonb_build_object('position',to_jsonb(v_position),'balance_after',v_account.balance+p_released);
end $$;

create or replace function public.paper_reconcile_v39(p_account_id text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_account public.paper_accounts;
    v_realized double precision;
    v_closed_fees double precision;
    v_open_margin double precision;
    v_open_entry_fees double precision;
    v_balance double precision;
    v_equity double precision;
    v_fees double precision;
    v_closed_count integer;
    v_open_count integer;
begin
    select * into v_account from public.paper_accounts where id=p_account_id for update;
    if not found then raise exception 'paper account % unavailable',p_account_id; end if;

    select coalesce(sum(net_pnl),0), coalesce(sum(greatest(0,coalesce(gross_pnl,0)-coalesce(net_pnl,0))),0), count(*)
      into v_realized,v_closed_fees,v_closed_count
    from public.paper_positions
    where account_id=p_account_id and status='closed' and execution_verified=true;

    select coalesce(sum(margin_usd),0), coalesce(sum(entry_fee),0), count(*)
      into v_open_margin,v_open_entry_fees,v_open_count
    from public.paper_positions
    where account_id=p_account_id and status='open';

    v_balance := v_account.initial_balance + v_realized - v_open_margin - v_open_entry_fees;
    v_equity := v_account.initial_balance + v_realized - v_open_entry_fees;
    v_fees := v_closed_fees + v_open_entry_fees;

    update public.paper_accounts set balance=v_balance,equity=v_equity,realized_pnl=v_realized,
        fees_paid=v_fees,updated_at=now() where id=p_account_id;

    return jsonb_build_object('account_id',p_account_id,'initial_balance',v_account.initial_balance,
        'valid_closed_trades',v_closed_count,'open_positions',v_open_count,'realized_pnl',v_realized,
        'fees_paid',v_fees,'balance',v_balance,'equity',v_equity,'applied',true);
end $$;

create or replace function public.paper_reset_v39(p_account_id text,p_initial_balance double precision)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_active integer;
begin
    perform 1 from public.paper_accounts where id=p_account_id for update;
    if not found then raise exception 'paper account % unavailable',p_account_id; end if;
    select count(*) into v_active from public.paper_positions where account_id=p_account_id and status in ('open','pending_entry');
    if v_active > 0 then return jsonb_build_object('ok',false,'reason','active_positions','active',v_active); end if;
    delete from public.paper_trades where account_id=p_account_id;
    delete from public.paper_positions where account_id=p_account_id;
    update public.paper_accounts set initial_balance=p_initial_balance,balance=p_initial_balance,equity=p_initial_balance,
        realized_pnl=0,fees_paid=0,status='active',updated_at=now() where id=p_account_id;
    return jsonb_build_object('ok',true,'initial_balance',p_initial_balance);
end $$;

revoke all on function public.paper_close_v39(uuid,double precision,text,double precision,double precision,double precision,double precision,double precision,timestamptz,jsonb,jsonb) from public, anon, authenticated;
revoke all on function public.paper_reconcile_v39(text) from public, anon, authenticated;
revoke all on function public.paper_reset_v39(text,double precision) from public, anon, authenticated;
grant execute on function public.paper_close_v39(uuid,double precision,text,double precision,double precision,double precision,double precision,double precision,timestamptz,jsonb,jsonb) to service_role;
grant execute on function public.paper_reconcile_v39(text) to service_role;
grant execute on function public.paper_reset_v39(text,double precision) to service_role;

notify pgrst, 'reload schema';
commit;
