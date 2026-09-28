-- Apply after 025. An authorised organiser can correct a rejected proof when
-- the exact credit is subsequently confirmed in the receiving account. The
-- original rejection remains in audit_logs and is copied into the next audit
-- row's previous_state. The payment, entry, proof and audit row commit together.
-- The API requires an explicit bank-credit checkbox; only its service-role
-- client can execute this RPC. The database also requires a fresh explanation.

CREATE OR REPLACE FUNCTION public.review_payment_proof_v2(
  p_proof_id uuid,
  p_reviewer_id uuid,
  p_decision text,
  p_note text,
  p_allow_any_admin boolean
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
  v_proof public.payment_proofs%ROWTYPE;
  v_registration public.registrations%ROWTYPE;
  v_tournament public.tournaments%ROWTYPE;
  v_payment public.payments%ROWTYPE;
  v_payment_json jsonb := NULL;
  v_before jsonb;
  v_expected_amount integer;
  v_tournament_id uuid;
  v_registration_id uuid;
  v_reconsidered boolean;
BEGIN
  -- reconsideration_026: health checks this exact implementation marker.
  IF p_decision IS NULL OR p_decision NOT IN ('approved', 'rejected') THEN
    RAISE EXCEPTION 'Decision must be approved or rejected';
  END IF;
  IF p_decision = 'rejected' AND length(btrim(COALESCE(p_note, ''))) < 5 THEN
    RAISE EXCEPTION 'A rejection needs a reason of at least 5 characters';
  END IF;
  SELECT tournament_id, registration_id
    INTO v_tournament_id, v_registration_id
  FROM public.payment_proofs WHERE id = p_proof_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Payment proof % does not exist', p_proof_id;
  END IF;
  -- Use the same tournament -> registration lock order as desk settlement.
  -- Once the entry is locked, a competing approval or desk payment cannot
  -- settle it first. Re-read the proof under its lock before deciding.
  SELECT * INTO v_tournament FROM public.tournaments
  WHERE id = v_tournament_id FOR UPDATE;
  SELECT * INTO v_registration FROM public.registrations
  WHERE id = v_registration_id AND tournament_id = v_tournament_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Payment proof is not linked to a valid registration';
  END IF;
  SELECT * INTO v_proof FROM public.payment_proofs
  WHERE id = p_proof_id FOR UPDATE;
  IF NOT FOUND OR v_proof.registration_id <> v_registration_id
     OR v_proof.tournament_id <> v_tournament_id THEN
    RAISE EXCEPTION 'Payment proof changed during review';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.profiles p WHERE p.id = p_reviewer_id
      AND p.role = 'admin'
      AND (
        COALESCE(p_allow_any_admin, false)
        OR v_tournament.owner_id IS NULL
        OR v_tournament.owner_id = p_reviewer_id OR EXISTS (
          SELECT 1 FROM public.tournament_access a
          WHERE a.tournament_id = v_proof.tournament_id
            AND a.user_id = p_reviewer_id AND a.status = 'approved'
            AND a.access_role = 'manager'
        )
      )
  ) THEN
    RAISE EXCEPTION 'Only this tournament owner or an approved manager may review payment proof';
  END IF;

  IF v_proof.status <> 'pending'
     AND NOT (v_proof.status = 'rejected' AND p_decision = 'approved') THEN
    IF v_proof.status = p_decision THEN
      IF v_proof.payment_id IS NOT NULL THEN
        SELECT * INTO v_payment FROM public.payments WHERE id = v_proof.payment_id;
        v_payment_json := to_jsonb(v_payment);
      END IF;
      RETURN jsonb_build_object('proof', to_jsonb(v_proof),
        'payment', v_payment_json, 'registration', to_jsonb(v_registration));
    END IF;
    RAISE EXCEPTION 'This payment proof has already been reviewed';
  END IF;
  v_reconsidered := v_proof.status = 'rejected';
  IF v_reconsidered AND length(btrim(COALESCE(p_note, ''))) < 15 THEN
    RAISE EXCEPTION 'Explain the corrected rejection in at least 15 characters';
  END IF;
  v_before := to_jsonb(v_proof);

  IF p_decision = 'approved' THEN
    IF v_registration.status = 'rejected'
       OR v_tournament.status IN ('cancelled', 'completed') THEN
      RAISE EXCEPTION 'A rejected entry or finished tournament cannot accept proof approval';
    END IF;
    IF v_registration.payment_status <> 'pending' THEN
      RAISE EXCEPTION 'This registration is already settled';
    END IF;
    v_expected_amount := COALESCE(v_registration.fee_paise,
      round(COALESCE(v_tournament.entry_fee, 0) * 100)::integer);
    IF v_expected_amount <= 0 OR v_proof.amount_paise <> v_expected_amount THEN
      RAISE EXCEPTION 'Proof amount does not match the registration fee';
    END IF;
    IF EXISTS (SELECT 1 FROM public.payments
      WHERE registration_id = v_registration.id AND status = 'paid') THEN
      RAISE EXCEPTION 'A payment is already recorded for this registration';
    END IF;
    IF v_reconsidered AND EXISTS (
      SELECT 1 FROM public.payment_proofs other
      WHERE other.registration_id = v_registration.id
        AND other.id <> v_proof.id AND other.status = 'pending'
    ) THEN
      RAISE EXCEPTION 'Review the newer pending proof before reconsidering this one';
    END IF;
    IF EXISTS (
      SELECT 1 FROM public.payments p
      WHERE p.method IN ('upi', 'bank_transfer', 'gpay_upi')
        AND p.status IN ('paid', 'refunded', 'refund_due')
        AND regexp_replace(upper(COALESCE(p.notes->>'reference', '')),
          '[^A-Z0-9]', '', 'g') = v_proof.transaction_reference
    ) THEN
      RAISE EXCEPTION 'This transaction reference is already recorded in the payment ledger';
    END IF;
    INSERT INTO public.payments (
      registration_id, tournament_id, razorpay_order_id,
      amount_paise, status, method, paid_at, notes
    ) VALUES (
      v_registration.id, v_proof.tournament_id, 'gpay-proof-' || v_proof.id::text,
      v_proof.amount_paise, 'paid', 'gpay_upi', timezone('utc', now()),
      jsonb_build_object('reference', v_proof.transaction_reference,
        'proof_id', v_proof.id, 'reviewed_by', p_reviewer_id,
        'reconsidered', v_reconsidered)
    ) RETURNING * INTO v_payment;
    v_payment_json := to_jsonb(v_payment);
    UPDATE public.registrations SET payment_status = 'paid', status = 'approved'
    WHERE id = v_registration.id RETURNING * INTO v_registration;
    UPDATE public.payment_proofs SET status = 'approved',
      reviewed_by = p_reviewer_id, reviewed_at = timezone('utc', now()),
      review_note = NULLIF(btrim(p_note), ''), payment_id = v_payment.id
    WHERE id = p_proof_id RETURNING * INTO v_proof;
  ELSE
    UPDATE public.payment_proofs SET status = 'rejected',
      reviewed_by = p_reviewer_id, reviewed_at = timezone('utc', now()),
      review_note = btrim(p_note)
    WHERE id = p_proof_id RETURNING * INTO v_proof;
  END IF;

  INSERT INTO public.audit_logs (
    user_id, action, entity_type, entity_id, previous_state, new_state,
    request_context
  ) VALUES (
    p_reviewer_id,
    CASE WHEN v_reconsidered THEN 'payment.proof_reconsidered_approved'
         ELSE 'payment.proof_' || p_decision END,
    'payment_proof', p_proof_id::text, v_before, to_jsonb(v_proof),
    jsonb_build_object('receiving_account_confirmed', p_decision = 'approved',
                       'reconsidered', v_reconsidered,
                       'review_note', btrim(COALESCE(p_note, '')))
  );
  RETURN jsonb_build_object('proof', to_jsonb(v_proof),
    'payment', v_payment_json, 'registration', to_jsonb(v_registration));
END;
$$;

REVOKE ALL ON FUNCTION public.review_payment_proof_v2(
  uuid, uuid, text, text, boolean
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.review_payment_proof_v2(
  uuid, uuid, text, text, boolean
) TO service_role;

-- A read-only probe differentiates this replacement from the 025 version,
-- which has the same function name and signature. The marker is part of the
-- function definition, so replacing it with an older version degrades health.
CREATE OR REPLACE FUNCTION public.payment_proof_reconsideration_ready()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
  SELECT COALESCE(
    position('reconsideration_026' IN pg_catalog.pg_get_functiondef(
      pg_catalog.to_regprocedure(
        'public.review_payment_proof_v2(uuid,uuid,text,text,boolean)'
      )
    )) > 0, false
  );
$$;

REVOKE ALL ON FUNCTION public.payment_proof_reconsideration_ready()
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.payment_proof_reconsideration_ready()
  TO service_role;
