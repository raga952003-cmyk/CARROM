-- Apply after 027. OCR is only a warning system; it cannot prove a transfer.
-- A clearly different destination on the submitted receipt must not be
-- approved as payment to this tournament's GPay account. Missing or masked
-- destination text remains eligible for independent bank-credit review.

CREATE OR REPLACE FUNCTION public.guard_payment_proof_receipt_approval()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
  v_analysis jsonb;
  v_similar boolean := false;
BEGIN
  -- payee_mismatch_028: the readiness probe identifies this exact guard.
  IF NEW.status <> 'approved' THEN
    RETURN NEW;
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF OLD.status = 'approved' THEN
      RETURN NEW;
    END IF;
    -- Check the evidence that existed before the approval write, so a caller
    -- cannot clear a warning in the same UPDATE as approving the proof.
    v_analysis := OLD.image_analysis;
  ELSE
    v_analysis := NEW.image_analysis;
  END IF;

  IF v_analysis->>'payeeMatches' = 'false' THEN
    RAISE EXCEPTION 'Receipt shows a different receiving account; request the correct payment proof';
  END IF;
  IF jsonb_typeof(v_analysis->'similarImageCount') = 'number' THEN
    v_similar := (v_analysis->>'similarImageCount')::numeric > 0;
  END IF;
  IF (v_similar OR v_analysis->>'referenceMatches' = 'false'
      OR v_analysis->>'amountMatches' = 'false')
     AND length(btrim(COALESCE(NEW.review_note, ''))) < 15 THEN
    RAISE EXCEPTION 'Explain the receipt warning and verified bank credit in at least 15 characters';
  END IF;
  RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.guard_payment_proof_receipt_approval()
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.guard_payment_proof_receipt_approval()
  TO service_role;

DROP TRIGGER IF EXISTS zz_guard_payment_proof_receipt_approval
  ON public.payment_proofs;
CREATE TRIGGER zz_guard_payment_proof_receipt_approval
  BEFORE INSERT OR UPDATE OF status ON public.payment_proofs
  FOR EACH ROW EXECUTE FUNCTION public.guard_payment_proof_receipt_approval();

CREATE OR REPLACE FUNCTION public.payment_proof_payee_guard_ready()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
  SELECT EXISTS (
    SELECT 1 FROM pg_catalog.pg_trigger trigger_row
    WHERE trigger_row.tgrelid = 'public.payment_proofs'::regclass
      AND trigger_row.tgname = 'zz_guard_payment_proof_receipt_approval'
      AND trigger_row.tgenabled IN ('O', 'A')
      AND trigger_row.tgfoid = pg_catalog.to_regprocedure(
        'public.guard_payment_proof_receipt_approval()')
  ) AND COALESCE(
    position('payee_mismatch_028' IN pg_catalog.pg_get_functiondef(
      pg_catalog.to_regprocedure(
        'public.guard_payment_proof_receipt_approval()')
    )) > 0, false
  );
$$;

REVOKE ALL ON FUNCTION public.payment_proof_payee_guard_ready()
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.payment_proof_payee_guard_ready()
  TO service_role;
