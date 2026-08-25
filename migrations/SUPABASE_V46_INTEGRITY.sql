-- V46 integrity migration. Run after V45.
-- Prefer the most complete duplicate before enforcing uniqueness.
WITH ranked AS (
  SELECT id,
         row_number() OVER (
           PARTITION BY metadata->>'fingerprint'
           ORDER BY
             CASE WHEN resolved_at IS NOT NULL OR real_result IS NOT NULL OR training_status='ready' THEN 0 ELSE 1 END,
             CASE WHEN real_result IS NOT NULL THEN 0 ELSE 1 END,
             coalesce(updated_at, created_at) DESC NULLS LAST,
             id DESC
         ) AS rn
  FROM public.learning_observations
  WHERE coalesce(metadata->>'fingerprint','') <> ''
)
DELETE FROM public.learning_observations lo USING ranked r
WHERE lo.id=r.id AND r.rn>1;

CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_observations_fingerprint
ON public.learning_observations ((metadata->>'fingerprint'))
WHERE metadata->>'fingerprint' IS NOT NULL;

-- Distributed V14 training lease for multiple hosts/containers.
CREATE TABLE IF NOT EXISTS public.model_training_leases_v46 (
  lock_name text PRIMARY KEY,
  token text NOT NULL,
  owner text,
  expires_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.model_training_leases_v46 ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.model_training_leases_v46 FROM PUBLIC, anon, authenticated;
GRANT ALL ON public.model_training_leases_v46 TO service_role;

CREATE OR REPLACE FUNCTION public.model_training_lease_acquire_v46(
  p_lock_name text, p_token text, p_owner text, p_ttl_seconds integer DEFAULT 3600
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE v_ok boolean := false;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('model-training:'||p_lock_name));
  DELETE FROM public.model_training_leases_v46 WHERE lock_name=p_lock_name AND expires_at<=now();
  INSERT INTO public.model_training_leases_v46(lock_name,token,owner,expires_at,updated_at)
  VALUES(p_lock_name,p_token,p_owner,now()+make_interval(secs=>greatest(60,p_ttl_seconds)),now())
  ON CONFLICT(lock_name) DO NOTHING;
  SELECT token=p_token INTO v_ok FROM public.model_training_leases_v46 WHERE lock_name=p_lock_name;
  RETURN coalesce(v_ok,false);
END; $$;

CREATE OR REPLACE FUNCTION public.model_training_lease_release_v46(p_lock_name text,p_token text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE n integer;
BEGIN
  DELETE FROM public.model_training_leases_v46 WHERE lock_name=p_lock_name AND token=p_token;
  GET DIAGNOSTICS n=ROW_COUNT; RETURN n>0;
END; $$;
REVOKE ALL ON FUNCTION public.model_training_lease_acquire_v46(text,text,text,integer) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.model_training_lease_release_v46(text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.model_training_lease_acquire_v46(text,text,text,integer) TO service_role;
GRANT EXECUTE ON FUNCTION public.model_training_lease_release_v46(text,text) TO service_role;
NOTIFY pgrst,'reload schema';
