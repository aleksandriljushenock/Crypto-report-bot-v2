begin;

alter table public.learning_observations
    add column if not exists quality_score double precision,
    add column if not exists calibrated_probability double precision,
    add column if not exists expected_value_pct double precision,
    add column if not exists quality_decision text,
    add column if not exists hedge_profile_version text;

create index if not exists learning_observations_quality_score_idx
    on public.learning_observations (quality_score desc nulls last);

create index if not exists learning_observations_expected_value_idx
    on public.learning_observations (expected_value_pct desc nulls last);

create index if not exists learning_observations_quality_decision_idx
    on public.learning_observations (quality_decision, signal_created_at desc);

create or replace view public.learning_signal_quality_dashboard as
select
    id,
    symbol,
    timeframe,
    signal_direction,
    signal_score,
    signal_confidence,
    quality_score,
    calibrated_probability,
    expected_value_pct,
    quality_decision,
    hedge_profile_version,
    outcome,
    price_change_pct,
    training_status,
    signal_created_at,
    resolved_at,
    metadata -> 'positive_profile_hits' as positive_profile_hits,
    metadata -> 'anti_profile_hits' as anti_profile_hits,
    metadata -> 'quality_rules' as quality_rules,
    metadata -> 'historical_evidence' as historical_evidence,
    real_result
from public.learning_observations;

commit;

notify pgrst, 'reload schema';

-- Chronos observability fields (safe to run repeatedly).
alter table public.learning_observations
    add column if not exists chronos_probability double precision,
    add column if not exists chronos_return_pct double precision,
    add column if not exists chronos_agreement boolean,
    add column if not exists chronos_model text,
    add column if not exists chronos_status text;

create index if not exists learning_observations_chronos_status_idx
    on public.learning_observations (chronos_status, signal_created_at desc);

create or replace view public.learning_signal_quality_dashboard as
select
    id, symbol, timeframe, signal_direction, signal_score, signal_confidence,
    quality_score, calibrated_probability, expected_value_pct, quality_decision,
    hedge_profile_version, chronos_probability, chronos_return_pct,
    chronos_agreement, chronos_model, chronos_status,
    outcome, price_change_pct, training_status, signal_created_at, resolved_at,
    metadata -> 'positive_profile_hits' as positive_profile_hits,
    metadata -> 'anti_profile_hits' as anti_profile_hits,
    metadata -> 'quality_rules' as quality_rules,
    metadata -> 'historical_evidence' as historical_evidence,
    metadata -> 'chronos' as chronos_details,
    real_result
from public.learning_observations;

notify pgrst, 'reload schema';
