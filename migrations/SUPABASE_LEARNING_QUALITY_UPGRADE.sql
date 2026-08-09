begin;

-- Keep run statuses aligned with cloud_model_store.py.
alter table public.training_runs
    drop constraint if exists training_runs_status_check;

update public.training_runs
set status = case
    when lower(coalesce(status, '')) in ('complete','success','succeeded','trained','done','ok','active','champion','challenger') then 'completed'
    when lower(coalesce(status, '')) in ('failure','error','errored') then 'failed'
    when lower(coalesce(status, '')) in ('collecting-data','collecting','training','started','in-progress','processing','running') then 'running'
    else 'pending'
end;

alter table public.training_runs
    alter column status set default 'pending';

alter table public.training_runs
    alter column status set not null;

alter table public.training_runs
    add constraint training_runs_status_check
    check (status in ('pending','running','completed','failed'));

-- Query paths used by restore, deduplication and diagnostics.
create index if not exists learning_observations_resolved_created_idx
    on public.learning_observations(created_at desc)
    where real_result is not null;

create index if not exists learning_observations_pending_resolve_idx
    on public.learning_observations(resolve_after)
    where training_status = 'pending';

create index if not exists learning_observations_symbol_timeframe_created_idx
    on public.learning_observations(symbol, timeframe, created_at desc);

create index if not exists learning_observations_metadata_gin_idx
    on public.learning_observations using gin(metadata);

create index if not exists training_runs_model_created_idx
    on public.training_runs(model_name, created_at desc);

create index if not exists model_registry_active_created_idx
    on public.model_registry(model_name, is_active, created_at desc);

commit;

notify pgrst, 'reload schema';
