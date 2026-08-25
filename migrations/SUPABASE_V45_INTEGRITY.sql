-- V45 integrity migration. Safe to run after V43/V44.
-- 1) Deduplicate learning observations before enforcing fingerprint uniqueness.
WITH ranked AS (
  SELECT id,
         row_number() OVER (
           PARTITION BY metadata->>'fingerprint'
           ORDER BY coalesce(signal_created_at, created_at) ASC, id ASC
         ) AS rn
  FROM public.learning_observations
  WHERE coalesce(metadata->>'fingerprint','') <> ''
)
DELETE FROM public.learning_observations lo
USING ranked r
WHERE lo.id = r.id AND r.rn > 1;

CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_observations_fingerprint
ON public.learning_observations ((metadata->>'fingerprint'))
WHERE metadata->>'fingerprint' IS NOT NULL;

-- Idempotent create by fingerprint. Outcome/update fields are applied by normal update_by_id.
CREATE OR REPLACE FUNCTION public.learning_observation_upsert_v45(p_row jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_fp text := p_row #>> '{metadata,fingerprint}';
  v_id public.learning_observations.id%TYPE;
  v public.learning_observations%rowtype;
BEGIN
  IF coalesce(v_fp,'') = '' THEN RAISE EXCEPTION 'metadata.fingerprint is required'; END IF;
  PERFORM pg_advisory_xact_lock(hashtext('learning_observation:' || v_fp));
  SELECT id INTO v_id FROM public.learning_observations
   WHERE metadata->>'fingerprint'=v_fp
   ORDER BY coalesce(signal_created_at,created_at) ASC LIMIT 1;
  IF v_id IS NOT NULL THEN RETURN jsonb_build_object('status','existing','id',v_id); END IF;
  v := jsonb_populate_record(NULL::public.learning_observations,p_row);
  INSERT INTO public.learning_observations(
    symbol,timeframe,signal_type,signal_direction,signal_score,signal_confidence,
    entry_price,target_price,stop_loss,market_price_at_signal,market_price_after,
    price_change_pct,max_favorable_excursion_pct,max_adverse_excursion_pct,outcome,outcome_score,
    features,smart_money_data,news_data,metadata,signal_created_at,resolve_after,resolved_at,
    training_status,training_run_id,real_result,quality_score,calibrated_probability,expected_value_pct,
    quality_decision,hedge_profile_version,chronos_probability,chronos_return_pct,chronos_agreement,
    chronos_model,chronos_status,created_at,updated_at
  ) VALUES (
    v.symbol,v.timeframe,v.signal_type,v.signal_direction,v.signal_score,v.signal_confidence,
    v.entry_price,v.target_price,v.stop_loss,v.market_price_at_signal,v.market_price_after,
    v.price_change_pct,v.max_favorable_excursion_pct,v.max_adverse_excursion_pct,v.outcome,v.outcome_score,
    v.features,v.smart_money_data,v.news_data,v.metadata,coalesce(v.signal_created_at,now()),
    coalesce(v.resolve_after,coalesce(v.signal_created_at,now())+interval '1 hour'),v.resolved_at,
    coalesce(v.training_status,'pending'),v.training_run_id,v.real_result,v.quality_score,v.calibrated_probability,
    v.expected_value_pct,v.quality_decision,v.hedge_profile_version,v.chronos_probability,v.chronos_return_pct,
    v.chronos_agreement,v.chronos_model,v.chronos_status,coalesce(v.created_at,now()),coalesce(v.updated_at,now())
  ) RETURNING id INTO v_id;
  RETURN jsonb_build_object('status','inserted','id',v_id);
END;
$$;

REVOKE ALL ON FUNCTION public.learning_observation_upsert_v45(jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.learning_observation_upsert_v45(jsonb) TO service_role;

-- Cross-instance compare-and-promote. Candidate can promote only if champion has not changed.
CREATE OR REPLACE FUNCTION public.adaptive_model_compare_promote_v45(
  p_row jsonb,
  p_promote boolean,
  p_expected_champion text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_version text := p_row->>'version';
  v_current text;
  v_status text := CASE WHEN p_promote THEN 'champion' ELSE 'candidate' END;
BEGIN
  IF coalesce(v_version,'') = '' THEN
    RAISE EXCEPTION 'version is required';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtext('adaptive_model_versions:champion'));
  SELECT version INTO v_current
  FROM public.adaptive_model_versions
  WHERE status='champion'
  ORDER BY activated_at DESC NULLS LAST, created_at DESC
  LIMIT 1;

  IF p_promote AND coalesce(v_current,'') IS DISTINCT FROM coalesce(p_expected_champion,'') THEN
    RETURN jsonb_build_object('status','champion-changed','current_champion',v_current,'expected_champion',p_expected_champion);
  END IF;

  IF p_promote THEN
    UPDATE public.adaptive_model_versions SET status='archived' WHERE status='champion';
  END IF;

  INSERT INTO public.adaptive_model_versions(
    version,status,algorithm,samples_train,samples_validation,metrics,
    model_json,trigger,created_at,activated_at
  ) VALUES (
    v_version,v_status,coalesce(p_row->>'algorithm','pure_python_logistic_v1'),
    coalesce((p_row->>'samples_train')::integer,0),coalesce((p_row->>'samples_validation')::integer,0),
    coalesce(p_row->'metrics','{}'::jsonb),coalesce(p_row->'model_json','{}'::jsonb),p_row->>'trigger',
    coalesce((p_row->>'created_at')::timestamptz,now()),
    CASE WHEN p_promote THEN coalesce((p_row->>'activated_at')::timestamptz,now()) ELSE NULL END
  );
  RETURN jsonb_build_object('status','ok','version',v_version,'promoted',p_promote,'previous_champion',v_current);
END;
$$;

REVOKE ALL ON FUNCTION public.adaptive_model_compare_promote_v45(jsonb,boolean,text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.adaptive_model_compare_promote_v45(jsonb,boolean,text) TO service_role;
NOTIFY pgrst, 'reload schema';
