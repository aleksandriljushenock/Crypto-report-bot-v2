-- V43: atomic persistence/promotion for adaptive_model_versions.
-- Safe to run repeatedly. Requires the v18 adaptive_model_versions table.
create or replace function public.adaptive_model_store_v43(p_row jsonb, p_promote boolean)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_version text := p_row->>'version';
    v_status text := case when p_promote then 'champion' else 'candidate' end;
begin
    if coalesce(v_version, '') = '' then
        raise exception 'version is required';
    end if;

    perform pg_advisory_xact_lock(hashtext('adaptive_model_versions:champion'));

    if p_promote then
        update public.adaptive_model_versions
           set status = 'archived'
         where status = 'champion';
    end if;

    insert into public.adaptive_model_versions(
        version,status,algorithm,samples_train,samples_validation,metrics,
        model_json,trigger,created_at,activated_at
    ) values (
        v_version,
        v_status,
        coalesce(p_row->>'algorithm','pure_python_logistic_v1'),
        coalesce((p_row->>'samples_train')::integer,0),
        coalesce((p_row->>'samples_validation')::integer,0),
        coalesce(p_row->'metrics','{}'::jsonb),
        coalesce(p_row->'model_json','{}'::jsonb),
        p_row->>'trigger',
        coalesce((p_row->>'created_at')::timestamptz, now()),
        case when p_promote then coalesce((p_row->>'activated_at')::timestamptz, now()) else null end
    );

    return jsonb_build_object('status','ok','version',v_version,'promoted',p_promote);
end;
$$;

revoke all on function public.adaptive_model_store_v43(jsonb, boolean) from public, anon, authenticated;
grant execute on function public.adaptive_model_store_v43(jsonb, boolean) to service_role;
notify pgrst, 'reload schema';
