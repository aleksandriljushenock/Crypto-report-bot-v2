-- Cloud-first learning synchronization for AI Self Learning MAX v14.
-- Safe to run repeatedly in Supabase SQL Editor.

begin;

-- The application uses exactly these lifecycle states.
alter table public.learning_observations
    drop constraint if exists learning_observations_training_status_check;

alter table public.learning_observations
    add constraint learning_observations_training_status_check
    check (training_status in ('pending', 'processing', 'ready', 'failed'));

update public.learning_observations
set training_status = case
    when real_result is not null then 'ready'
    when training_status in ('resolved', 'completed') then 'ready'
    when training_status not in ('pending', 'processing', 'ready', 'failed') or training_status is null then 'pending'
    else training_status
end;

-- Every new row receives a first resolution deadline even if an old caller omitted it.
update public.learning_observations
set signal_created_at = coalesce(signal_created_at, created_at, now())
where signal_created_at is null;

update public.learning_observations
set resolve_after = signal_created_at + interval '1 hour'
where resolve_after is null;

create index if not exists learning_observations_status_resolve_idx
    on public.learning_observations(training_status, resolve_after);

create index if not exists learning_observations_signal_created_idx
    on public.learning_observations(signal_created_at desc);

create index if not exists learning_observations_fingerprint_idx
    on public.learning_observations ((metadata->>'fingerprint'));

create index if not exists learning_observations_real_result_idx
    on public.learning_observations(signal_created_at desc)
    where real_result is not null;

-- Remove duplicate cloud observations while retaining the oldest durable row.
with ranked as (
    select id,
           row_number() over (
               partition by metadata->>'fingerprint'
               order by created_at asc, id asc
           ) as rn
    from public.learning_observations
    where coalesce(metadata->>'fingerprint', '') <> ''
)
delete from public.learning_observations o
using ranked r
where o.id = r.id and r.rn > 1;

commit;
