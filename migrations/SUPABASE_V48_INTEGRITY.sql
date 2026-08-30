-- V48: model namespace isolation, compare-and-promote, and fencing-aware training lease.
-- Run after V47.

ALTER TABLE public.model_training_leases_v46 ADD COLUMN IF NOT EXISTS generation bigint NOT NULL DEFAULT 0;
-- Normalize the historical namespace spelling before enforcing V48 single-authority semantics.
DO $$
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('model-registry:learning-v14'));
  -- If the canonical namespace already has an active champion, retire the legacy
  -- champion before renaming to avoid the partial unique-index collision.
  IF EXISTS (SELECT 1 FROM public.model_registry WHERE model_name='learning-v14' AND is_active=true) THEN
    UPDATE public.model_registry SET is_active=false,status=CASE WHEN status='active' THEN 'retired' ELSE status END
      WHERE model_name='learning-engine-v14' AND is_active=true;
  END IF;
  UPDATE public.model_registry SET model_name='learning-v14' WHERE model_name='learning-engine-v14';
END $$;
CREATE OR REPLACE FUNCTION public.model_registry_promote_v48(
    p_model_name text,
    p_model_version text,
    p_expected_version text DEFAULT NULL,
    p_lease_token text DEFAULT NULL,
    p_lease_generation bigint DEFAULT NULL
) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE
    v_current text;
    v_target_exists boolean;
BEGIN
    IF p_model_name IS NULL OR btrim(p_model_name)='' OR p_model_version IS NULL OR btrim(p_model_version)='' THEN RETURN false; END IF;
    PERFORM pg_advisory_xact_lock(hashtext('model-registry:'||p_model_name));
    IF p_lease_token IS NOT NULL THEN
        IF NOT EXISTS(SELECT 1 FROM public.model_training_leases_v46 WHERE lock_name='v14-training' AND token=p_lease_token AND generation=p_lease_generation AND expires_at>now()) THEN
            RETURN false;
        END IF;
    END IF;
    SELECT model_version INTO v_current FROM public.model_registry
     WHERE model_name=p_model_name AND is_active=true
     ORDER BY activated_at DESC NULLS LAST,created_at DESC,id DESC LIMIT 1 FOR UPDATE;
    IF p_expected_version IS NOT NULL AND coalesce(v_current,'')<>p_expected_version THEN RETURN false; END IF;
    SELECT EXISTS(SELECT 1 FROM public.model_registry WHERE model_name=p_model_name AND model_version=p_model_version) INTO v_target_exists;
    IF NOT v_target_exists THEN RETURN false; END IF;
    UPDATE public.model_registry SET is_active=false WHERE model_name=p_model_name AND is_active=true AND model_version<>p_model_version;
    UPDATE public.model_registry SET is_active=true,status='active',activated_at=now() WHERE model_name=p_model_name AND model_version=p_model_version;
    RETURN FOUND;
END $$;

-- Lease generation is a fencing token. Every successful acquire increments it.
CREATE OR REPLACE FUNCTION public.model_training_lease_acquire_v48(
    p_lock_name text,p_token text,p_owner text,p_ttl_seconds integer DEFAULT 900
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE v_row public.model_training_leases_v46; v_generation bigint;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('model-training:'||p_lock_name));
    SELECT * INTO v_row FROM public.model_training_leases_v46 WHERE lock_name=p_lock_name FOR UPDATE;
    IF FOUND AND v_row.expires_at>now() AND v_row.token<>p_token THEN RETURN jsonb_build_object('ok',false); END IF;
    v_generation := coalesce(v_row.generation,0)+1;
    INSERT INTO public.model_training_leases_v46(lock_name,token,owner,expires_at,updated_at,generation)
    VALUES(p_lock_name,p_token,p_owner,now()+make_interval(secs=>greatest(60,p_ttl_seconds)),now(),v_generation)
    ON CONFLICT(lock_name) DO UPDATE SET token=excluded.token,owner=excluded.owner,expires_at=excluded.expires_at,updated_at=excluded.updated_at,generation=excluded.generation;
    RETURN jsonb_build_object('ok',true,'generation',v_generation);
END $$;
CREATE OR REPLACE FUNCTION public.model_training_lease_renew_v48(
    p_lock_name text,p_token text,p_generation bigint,p_ttl_seconds integer DEFAULT 900
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE n integer; BEGIN
    UPDATE public.model_training_leases_v46 SET expires_at=now()+make_interval(secs=>greatest(60,p_ttl_seconds)),updated_at=now()
     WHERE lock_name=p_lock_name AND token=p_token AND generation=p_generation AND expires_at>now();
    GET DIAGNOSTICS n=ROW_COUNT; RETURN n=1;
END $$;
CREATE OR REPLACE FUNCTION public.model_training_lease_release_v48(
    p_lock_name text,p_token text,p_generation bigint
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE n integer; BEGIN
    DELETE FROM public.model_training_leases_v46 WHERE lock_name=p_lock_name AND token=p_token AND generation=p_generation;
    GET DIAGNOSTICS n=ROW_COUNT; RETURN n=1;
END $$;
CREATE OR REPLACE FUNCTION public.model_training_lease_running_v48(p_lock_name text)
RETURNS boolean LANGUAGE sql SECURITY DEFINER SET search_path=public AS $$
SELECT EXISTS(SELECT 1 FROM public.model_training_leases_v46 WHERE lock_name=p_lock_name AND expires_at>now())
$$;

REVOKE ALL ON FUNCTION public.model_registry_promote_v48(text,text,text,text,bigint) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.model_training_lease_acquire_v48(text,text,text,integer) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.model_training_lease_renew_v48(text,text,bigint,integer) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.model_training_lease_release_v48(text,text,bigint) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.model_training_lease_running_v48(text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.model_registry_promote_v48(text,text,text,text,bigint) TO service_role;
GRANT EXECUTE ON FUNCTION public.model_training_lease_acquire_v48(text,text,text,integer) TO service_role;
GRANT EXECUTE ON FUNCTION public.model_training_lease_renew_v48(text,text,bigint,integer) TO service_role;
GRANT EXECUTE ON FUNCTION public.model_training_lease_release_v48(text,text,bigint) TO service_role;
GRANT EXECUTE ON FUNCTION public.model_training_lease_running_v48(text) TO service_role;
NOTIFY pgrst,'reload schema';

CREATE OR REPLACE FUNCTION public.paper_void_execution_v48(p_position_id uuid,p_reason text,p_closed_at timestamptz)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE v_position public.paper_positions; BEGIN
 SELECT * INTO v_position FROM public.paper_positions WHERE id=p_position_id FOR UPDATE;
 IF NOT FOUND OR v_position.status<>'open' THEN RETURN NULL; END IF;
 UPDATE public.paper_positions SET status='void_data',close_reason=p_reason,closed_at=p_closed_at,updated_at=p_closed_at,
   execution_verified=false,execution_audit=coalesce(execution_audit,'{}'::jsonb)||jsonb_build_object('void_reason',p_reason)
 WHERE id=p_position_id AND status='open' RETURNING * INTO v_position;
 UPDATE public.paper_accounts SET balance=balance+coalesce(v_position.margin_usd,0),updated_at=p_closed_at WHERE id=v_position.account_id;
 RETURN to_jsonb(v_position);
END $$;

CREATE OR REPLACE FUNCTION public.paper_reconcile_v48(p_account_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE v_account public.paper_accounts; v_realized double precision; v_closed_fees double precision; v_void_fees double precision;
 v_open_margin double precision; v_open_entry_fees double precision; v_balance double precision; v_equity double precision; v_fees double precision; v_closed_count integer; v_open_count integer;
BEGIN
 SELECT * INTO v_account FROM public.paper_accounts WHERE id=p_account_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'paper account % unavailable',p_account_id; END IF;
 SELECT coalesce(sum(net_pnl),0),coalesce(sum(greatest(0,coalesce(gross_pnl,0)-coalesce(net_pnl,0))),0),count(*)
 INTO v_realized,v_closed_fees,v_closed_count FROM public.paper_positions WHERE account_id=p_account_id AND status='closed' AND execution_verified=true;
 SELECT coalesce(sum(entry_fee),0) INTO v_void_fees FROM public.paper_positions WHERE account_id=p_account_id AND status='void_data';
 SELECT coalesce(sum(margin_usd),0),coalesce(sum(entry_fee),0),count(*) INTO v_open_margin,v_open_entry_fees,v_open_count
 FROM public.paper_positions WHERE account_id=p_account_id AND status='open';
 v_balance:=v_account.initial_balance+v_realized-v_open_margin-v_open_entry_fees-v_void_fees;
 v_equity:=v_account.initial_balance+v_realized-v_open_entry_fees-v_void_fees;
 v_fees:=v_closed_fees+v_open_entry_fees+v_void_fees;
 UPDATE public.paper_accounts SET balance=v_balance,equity=v_equity,realized_pnl=v_realized,fees_paid=v_fees,updated_at=now() WHERE id=p_account_id;
 RETURN jsonb_build_object('account_id',p_account_id,'valid_closed_trades',v_closed_count,'open_positions',v_open_count,'balance',v_balance,'equity',v_equity,'fees_paid',v_fees,'applied',true);
END $$;
REVOKE ALL ON FUNCTION public.paper_void_execution_v48(uuid,text,timestamptz) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.paper_reconcile_v48(text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.paper_void_execution_v48(uuid,text,timestamptz) TO service_role;
GRANT EXECUTE ON FUNCTION public.paper_reconcile_v48(text) TO service_role;
NOTIFY pgrst,'reload schema';
