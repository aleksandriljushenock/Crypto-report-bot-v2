-- V49 Execution Integrity. Run after V48. Safe to re-run.
DO $$
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('model-registry:learning-v14'));
  IF EXISTS (SELECT 1 FROM public.model_registry WHERE model_name='learning-v14' AND is_active=true) THEN
    UPDATE public.model_registry SET is_active=false,status=CASE WHEN status='active' THEN 'retired' ELSE status END
      WHERE model_name='learning-engine-v14' AND is_active=true;
  END IF;
  UPDATE public.model_registry SET model_name='learning-v14' WHERE model_name='learning-engine-v14';
END $$;

-- Durable per-horizon terminal outcome marker. It merges rather than replacing
-- existing metadata so a later terminal horizon cannot erase an earlier one.
CREATE OR REPLACE FUNCTION public.learning_mark_terminal_outcome_v49(
  p_fingerprint text,p_horizon text,p_reason text
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE n integer; BEGIN
  UPDATE public.learning_observations
     SET metadata=coalesce(metadata,'{}'::jsonb) || jsonb_build_object(
       'terminal_outcomes', coalesce(metadata->'terminal_outcomes','{}'::jsonb) || jsonb_build_object(p_horizon,p_reason)),
       updated_at=now()
   WHERE metadata->>'fingerprint'=p_fingerprint;
  GET DIAGNOSTICS n=ROW_COUNT; RETURN n>0;
END $$;
REVOKE ALL ON FUNCTION public.learning_mark_terminal_outcome_v49(text,text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.learning_mark_terminal_outcome_v49(text,text,text) TO service_role;
NOTIFY pgrst,'reload schema';
