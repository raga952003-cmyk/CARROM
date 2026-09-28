-- Apply after 022. Desk collection, entry approval and both audit rows happen
-- in one Postgres transaction. Retrying the same method/reference is safe.
-- The Supabase CLI is not installed in this workspace, so this file follows
-- the existing numbered migration convention for SQL Editor deployment.

CREATE OR REPLACE FUNCTION public.record_manual_entry_payment(
  p_registration_id uuid,
  p_actor_id uuid,
  p_method text,
  p_reference text,
  p_allow_any_admin boolean DEFAULT false
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
  v_tournament_id uuid;
  v_tournament public.tournaments%ROWTYPE;
  v_registration public.registrations%ROWTYPE;
  v_before public.registrations%ROWTYPE;
  v_payment public.payments%ROWTYPE;
  v_reference text;
  v_fee integer;
BEGIN
  IF p_method NOT IN ('cash', 'upi', 'bank_transfer') THEN
    RAISE EXCEPTION 'Choose cash, UPI or bank transfer';
  END IF;
  v_reference := btrim(COALESCE(p_reference, ''));
  IF p_method IN ('upi', 'bank_transfer') THEN
    v_reference := regexp_replace(upper(v_reference), '[^A-Z0-9]', '', 'g');
    IF length(v_reference) NOT BETWEEN 6 AND 80 THEN
      RAISE EXCEPTION 'Enter a 6 to 80 character bank or UPI transaction reference';
    END IF;
  ELSIF length(v_reference) NOT BETWEEN 3 AND 120 THEN
    RAISE EXCEPTION 'Enter a 3 to 120 character cash receipt reference';
  END IF;

  SELECT tournament_id INTO v_tournament_id FROM public.registrations
  WHERE id = p_registration_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Registration does not exist';
  END IF;
  -- Match draw/schedule lock order: tournament first, then entry. Locking the
  -- entry serializes this RPC with GPay proof review and competing desk calls.
  SELECT * INTO v_tournament FROM public.tournaments
  WHERE id = v_tournament_id FOR UPDATE;
  SELECT * INTO v_registration FROM public.registrations
  WHERE id = p_registration_id AND tournament_id = v_tournament_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Registration changed; reload it';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.profiles actor WHERE actor.id = p_actor_id
      AND actor.role = 'admin'
      AND (
        COALESCE(p_allow_any_admin, false)
        OR v_tournament.owner_id IS NULL
        OR v_tournament.owner_id = p_actor_id
        OR EXISTS (
          SELECT 1 FROM public.tournament_access ta
          WHERE ta.tournament_id = v_tournament_id
            AND ta.user_id = p_actor_id
            AND ta.status = 'approved'
            AND ta.access_role = 'manager'
        )
      )
  ) THEN
    RAISE EXCEPTION 'Only this tournament owner or an approved manager may record payment';
  END IF;

  SELECT * INTO v_payment FROM public.payments
  WHERE registration_id = p_registration_id AND status = 'paid'
  FOR UPDATE;
  IF FOUND THEN
    -- A response can be lost after commit. The exact same desk request must
    -- return its original ledger row instead of charging/recording a second one.
    IF v_payment.razorpay_order_id NOT LIKE 'manual-%'
       OR v_payment.method IS DISTINCT FROM p_method
       OR v_payment.notes->>'reference' IS DISTINCT FROM v_reference THEN
      RAISE EXCEPTION 'A different payment is already recorded for this entry';
    END IF;
    IF v_registration.payment_status = 'pending'
       AND v_registration.status <> 'rejected'
       AND v_tournament.status NOT IN ('cancelled', 'completed') THEN
      v_before := v_registration;
      UPDATE public.registrations SET payment_status = 'paid', status = 'approved'
      WHERE id = p_registration_id RETURNING * INTO v_registration;
      INSERT INTO public.audit_logs (
        user_id, action, entity_type, entity_id, previous_state, new_state
      ) VALUES (
        p_actor_id, 'payment.registration_reconciled', 'registration',
        p_registration_id::text, to_jsonb(v_before), to_jsonb(v_registration)
      );
    END IF;
    RETURN jsonb_build_object('payment', to_jsonb(v_payment),
                              'registration', to_jsonb(v_registration));
  END IF;

  IF v_registration.status = 'rejected'
     OR v_tournament.status IN ('cancelled', 'completed') THEN
    RAISE EXCEPTION 'A rejected entry or finished tournament cannot accept payment';
  END IF;
  IF v_registration.payment_status <> 'pending' THEN
    RAISE EXCEPTION 'This entry is already settled';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.payment_proofs proof
    WHERE proof.registration_id = p_registration_id AND proof.status = 'pending'
  ) THEN
    RAISE EXCEPTION 'A GPay proof is awaiting review; resolve it before desk collection';
  END IF;
  v_fee := COALESCE(v_registration.fee_paise,
    round(COALESCE(v_tournament.entry_fee, 0) * 100)::integer);
  IF v_fee <= 0 THEN
    RAISE EXCEPTION 'This entry has no fee to collect';
  END IF;
  IF p_method IN ('upi', 'bank_transfer') AND EXISTS (
    SELECT 1 FROM public.payment_proofs proof
    WHERE proof.transaction_reference = v_reference
  ) THEN
    RAISE EXCEPTION 'This transaction reference was already submitted as payment proof';
  END IF;
  IF p_method IN ('upi', 'bank_transfer') AND EXISTS (
    SELECT 1 FROM public.payments previous
    WHERE previous.method IN ('upi', 'bank_transfer', 'gpay_upi')
      AND previous.status IN ('paid', 'refunded', 'refund_due')
      AND regexp_replace(upper(COALESCE(previous.notes->>'reference', '')),
            '[^A-Z0-9]', '', 'g') = v_reference
  ) THEN
    RAISE EXCEPTION 'This bank or UPI transaction reference is already recorded';
  END IF;

  v_before := v_registration;
  INSERT INTO public.payments (
    registration_id, tournament_id, razorpay_order_id,
    amount_paise, status, method, paid_at, notes
  ) VALUES (
    p_registration_id, v_tournament_id,
    'manual-' || gen_random_uuid()::text,
    v_fee, 'paid', p_method, timezone('utc', now()),
    jsonb_build_object('reference', v_reference, 'recorded_by', p_actor_id)
  ) RETURNING * INTO v_payment;
  UPDATE public.registrations SET payment_status = 'paid', status = 'approved'
  WHERE id = p_registration_id RETURNING * INTO v_registration;
  INSERT INTO public.audit_logs (
    user_id, action, entity_type, entity_id, previous_state, new_state
  ) VALUES (
    p_actor_id, 'payment.manual_recorded', 'payment', v_payment.id::text,
    NULL, to_jsonb(v_payment)
  ), (
    p_actor_id, 'registration.auto_approved_after_payment', 'registration',
    p_registration_id::text, to_jsonb(v_before), to_jsonb(v_registration)
  );
  RETURN jsonb_build_object('payment', to_jsonb(v_payment),
                            'registration', to_jsonb(v_registration));
END;
$$;

REVOKE ALL ON FUNCTION public.record_manual_entry_payment(
  uuid, uuid, text, text, boolean
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.record_manual_entry_payment(
  uuid, uuid, text, text, boolean
) TO service_role;
