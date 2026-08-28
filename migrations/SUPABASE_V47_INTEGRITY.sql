-- V47: renewable distributed training lease + atomic V14 cloud champion. Run after V46.
ALTER TABLE public.model_registry ADD COLUMN IF NOT EXISTS model_name text NOT NULL DEFAULT 'learning-v14';
CREATE OR REPLACE FUNCTION public.model_training_lease_acquire_v47(p_lock_name text,p_token text,p_owner text,p_ttl_seconds integer DEFAULT 1800) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE ok boolean; BEGIN
 PERFORM pg_advisory_xact_lock(hashtext('model-training:'||p_lock_name));
 DELETE FROM public.model_training_leases_v46 WHERE lock_name=p_lock_name AND expires_at<=now();
 INSERT INTO public.model_training_leases_v46(lock_name,token,owner,expires_at,updated_at) VALUES(p_lock_name,p_token,p_owner,now()+make_interval(secs=>greatest(60,p_ttl_seconds)),now()) ON CONFLICT(lock_name) DO NOTHING;
 SELECT token=p_token INTO ok FROM public.model_training_leases_v46 WHERE lock_name=p_lock_name; RETURN coalesce(ok,false); END $$;
CREATE OR REPLACE FUNCTION public.model_training_lease_renew_v47(p_lock_name text,p_token text,p_ttl_seconds integer DEFAULT 1800) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE n integer; BEGIN UPDATE public.model_training_leases_v46 SET expires_at=now()+make_interval(secs=>greatest(60,p_ttl_seconds)),updated_at=now() WHERE lock_name=p_lock_name AND token=p_token AND expires_at>now(); GET DIAGNOSTICS n=ROW_COUNT; RETURN n>0; END $$;
CREATE OR REPLACE FUNCTION public.model_training_lease_release_v47(p_lock_name text,p_token text) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE n integer; BEGIN DELETE FROM public.model_training_leases_v46 WHERE lock_name=p_lock_name AND token=p_token; GET DIAGNOSTICS n=ROW_COUNT; RETURN n>0; END $$;
CREATE OR REPLACE FUNCTION public.model_training_lease_running_v47(p_lock_name text) RETURNS boolean LANGUAGE sql SECURITY DEFINER SET search_path=public AS $$ SELECT EXISTS(SELECT 1 FROM public.model_training_leases_v46 WHERE lock_name=p_lock_name AND expires_at>now()) $$;
CREATE OR REPLACE FUNCTION public.model_registry_promote_v47(p_model_name text,p_model_version text) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE n integer; BEGIN
 PERFORM pg_advisory_xact_lock(hashtext('model-registry:'||coalesce(p_model_name,'learning-v14')));
 UPDATE public.model_registry SET is_active=false WHERE is_active=true AND model_version<>p_model_version;
 UPDATE public.model_registry SET is_active=true,status='active',activated_at=now() WHERE model_version=p_model_version; GET DIAGNOSTICS n=ROW_COUNT; RETURN n=1; END $$;
REVOKE ALL ON FUNCTION public.model_training_lease_acquire_v47(text,text,text,integer) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.model_training_lease_renew_v47(text,text,integer) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.model_training_lease_release_v47(text,text) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.model_training_lease_running_v47(text) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.model_registry_promote_v47(text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.model_training_lease_acquire_v47(text,text,text,integer) TO service_role;
GRANT EXECUTE ON FUNCTION public.model_training_lease_renew_v47(text,text,integer) TO service_role;
GRANT EXECUTE ON FUNCTION public.model_training_lease_release_v47(text,text) TO service_role;
GRANT EXECUTE ON FUNCTION public.model_training_lease_running_v47(text) TO service_role;
GRANT EXECUTE ON FUNCTION public.model_registry_promote_v47(text,text) TO service_role;
WITH ranked AS (SELECT id,row_number() OVER (PARTITION BY model_name ORDER BY activated_at DESC NULLS LAST,created_at DESC,id DESC) rn FROM public.model_registry WHERE is_active=true) UPDATE public.model_registry m SET is_active=false FROM ranked r WHERE m.id=r.id AND r.rn>1;
CREATE UNIQUE INDEX IF NOT EXISTS uq_model_registry_one_active_v47 ON public.model_registry (model_name) WHERE is_active=true;
NOTIFY pgrst,'reload schema';
