-- Final refactor hardening migration (v24). Safe to run repeatedly.
begin;

-- Canonical Paper execution lifecycle.
alter table if exists public.paper_positions drop constraint if exists paper_positions_status_check;
alter table if exists public.paper_positions
    add constraint paper_positions_status_check
    check (status in ('pending_entry','open','closed','cancelled'));

alter table if exists public.paper_positions add column if not exists signal_entry_price double precision;
alter table if exists public.paper_positions add column if not exists entry_zone_low double precision;
alter table if exists public.paper_positions add column if not exists entry_zone_high double precision;
alter table if exists public.paper_positions add column if not exists trigger_price double precision;
alter table if exists public.paper_positions add column if not exists pending_until timestamptz;
alter table if exists public.paper_positions add column if not exists pending_reason text;
alter table if exists public.paper_positions add column if not exists fill_price_source text;
alter table if exists public.paper_positions add column if not exists execution_audit jsonb not null default '{}'::jsonb;

create index if not exists paper_positions_pending_idx
    on public.paper_positions(status, pending_until)
    where status = 'pending_entry';
create index if not exists paper_positions_valid_learning_idx
    on public.paper_positions(closed_at desc)
    where status = 'closed' and fill_price_source is not null;
create index if not exists paper_positions_fingerprint_idx
    on public.paper_positions(fingerprint);

-- Remove legacy duplicated close rows before enforcing idempotency.
delete from public.paper_trades a
using public.paper_trades b
where a.position_id = b.position_id
  and a.position_id is not null
  and a.ctid < b.ctid;
create unique index if not exists paper_trades_position_id_uidx
    on public.paper_trades(position_id)
    where position_id is not null;

-- Rebuild dashboard to avoid CREATE OR REPLACE column-name/order conflicts.
drop view if exists public.learning_signal_quality_dashboard;
create view public.learning_signal_quality_dashboard
with (security_invoker = true) as
select
    id, symbol, timeframe, signal_direction, signal_score, signal_confidence,
    quality_score, calibrated_probability, expected_value_pct, quality_decision,
    hedge_profile_version, chronos_probability, chronos_return_pct,
    chronos_agreement, chronos_model, chronos_status, outcome,
    price_change_pct, training_status, signal_created_at, resolved_at,
    metadata -> 'positive_profile_hits' as positive_profile_hits,
    metadata -> 'anti_profile_hits' as anti_profile_hits,
    metadata -> 'quality_rules' as quality_rules,
    metadata -> 'historical_evidence' as historical_evidence,
    metadata -> 'chronos' as chronos_details,
    real_result
from public.learning_observations;

comment on view public.learning_signal_quality_dashboard is
'Learning quality dashboard using SECURITY INVOKER so caller RLS/permissions apply.';

notify pgrst, 'reload schema';
commit;
