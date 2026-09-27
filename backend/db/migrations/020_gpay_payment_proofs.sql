-- Apply after 019. GPay proof is evidence to review, never proof that funds
-- arrived. Only an organiser who sees the matching bank/UPI credit approves.

ALTER TABLE public.tournaments
  ADD COLUMN IF NOT EXISTS gpay_upi_id text;
ALTER TABLE public.tournaments
  DROP CONSTRAINT IF EXISTS tournaments_gpay_upi_id_check;
ALTER TABLE public.tournaments
  ADD CONSTRAINT tournaments_gpay_upi_id_check CHECK (
    gpay_upi_id IS NULL OR
    gpay_upi_id ~ '^[6-9][0-9]{9}$' OR
    gpay_upi_id ~ '^[A-Za-z0-9._-]{2,100}@[A-Za-z0-9.-]{2,100}$'
  );

-- This bucket is deliberately private. The API uploads and signs short-lived
-- review URLs with its service key; no browser can list or overwrite proofs.
INSERT INTO storage.buckets (
  id, name, public, file_size_limit, allowed_mime_types
) VALUES (
  'payment-proofs', 'payment-proofs', false, 5242880,
  ARRAY['image/jpeg', 'image/png', 'image/webp', 'application/pdf']::text[]
)
ON CONFLICT (id) DO UPDATE SET
  public = false,
  file_size_limit = EXCLUDED.file_size_limit,
  allowed_mime_types = EXCLUDED.allowed_mime_types;

DROP POLICY IF EXISTS deny_browser_payment_proofs ON storage.objects;
CREATE POLICY deny_browser_payment_proofs ON storage.objects
  AS RESTRICTIVE FOR ALL TO anon, authenticated
  USING (bucket_id <> 'payment-proofs')
  WITH CHECK (bucket_id <> 'payment-proofs');

-- The composite foreign key below proves that a proof's tournament is the
-- same event as its registration; a separate FK on each ID would not.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_registrations_id_tournament
  ON public.registrations(id, tournament_id);

CREATE TABLE IF NOT EXISTS public.payment_proofs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  registration_id uuid NOT NULL,
  tournament_id uuid NOT NULL REFERENCES public.tournaments(id) ON DELETE RESTRICT,
  CONSTRAINT payment_proofs_registration_tournament_fkey
    FOREIGN KEY (registration_id, tournament_id)
    REFERENCES public.registrations(id, tournament_id) ON DELETE RESTRICT,
  submitted_by uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  transaction_reference text NOT NULL
    CHECK (transaction_reference ~ '^[A-Z0-9]{6,80}$'),
  content_sha256 text NOT NULL
    CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  storage_bucket text NOT NULL DEFAULT 'payment-proofs'
    CHECK (storage_bucket = 'payment-proofs'),
  object_path text NOT NULL,
  mime_type text NOT NULL CHECK (mime_type IN (
    'image/jpeg', 'image/png', 'image/webp', 'application/pdf'
  )),
  content_size_bytes integer NOT NULL
    CHECK (content_size_bytes BETWEEN 1 AND 5242880),
  amount_paise integer NOT NULL CHECK (amount_paise > 0),
  -- Keep the payee shown when this proof was submitted, even if the
  -- tournament later changes its GPay number or UPI address.
  payee_upi_id text NOT NULL CHECK (
    payee_upi_id ~ '^[6-9][0-9]{9}$' OR
    payee_upi_id ~ '^[A-Za-z0-9._-]{2,100}@[A-Za-z0-9.-]{2,100}$'
  ),
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'approved', 'rejected')),
  submitted_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
  reviewed_by uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  reviewed_at timestamptz,
  review_note text,
  payment_id uuid UNIQUE REFERENCES public.payments(id) ON DELETE RESTRICT,
  CONSTRAINT payment_proofs_path_check CHECK (
    object_path = tournament_id::text || '/' || registration_id::text || '/'
      || id::text || '.' || CASE mime_type
        WHEN 'image/jpeg' THEN 'jpg'
        WHEN 'image/png' THEN 'png'
        WHEN 'image/webp' THEN 'webp'
        WHEN 'application/pdf' THEN 'pdf'
      END
  ),
  CONSTRAINT payment_proofs_review_check CHECK (
    (status = 'pending' AND reviewed_by IS NULL AND reviewed_at IS NULL
      AND payment_id IS NULL)
    OR (status = 'rejected' AND reviewed_by IS NOT NULL
      AND reviewed_at IS NOT NULL AND payment_id IS NULL)
    OR (status = 'approved' AND reviewed_by IS NOT NULL
      AND reviewed_at IS NOT NULL AND payment_id IS NOT NULL)
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_payment_proof_reference
  ON public.payment_proofs(transaction_reference);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_payment_proof_content_hash
  ON public.payment_proofs(content_sha256);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_payment_proof_object_path
  ON public.payment_proofs(object_path);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_pending_payment_proof_per_registration
  ON public.payment_proofs(registration_id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_payment_proofs_review_queue
  ON public.payment_proofs(tournament_id, submitted_at) WHERE status = 'pending';

ALTER TABLE public.payment_proofs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.payment_proofs FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE public.payment_proofs TO service_role;

-- Normalize old desk-entered references before a new receipt is uploaded.
-- A functional unique index in 021 remains the concurrent-write backstop.
CREATE OR REPLACE FUNCTION public.payment_reference_claimed(p_reference text)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
  SELECT length(regexp_replace(upper(COALESCE(p_reference, '')),
           '[^A-Z0-9]', '', 'g')) >= 6
    AND EXISTS (
      SELECT 1 FROM public.payments p
      WHERE p.method IN ('upi', 'bank_transfer', 'gpay_upi')
        AND p.status IN ('paid', 'refunded', 'refund_due')
        AND regexp_replace(upper(COALESCE(p.notes->>'reference', '')),
          '[^A-Z0-9]', '', 'g') =
          regexp_replace(upper(COALESCE(p_reference, '')),
            '[^A-Z0-9]', '', 'g')
    );
$$;
REVOKE ALL ON FUNCTION public.payment_reference_claimed(text)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.payment_reference_claimed(text)
  TO service_role;

-- A service-role-only review transaction keeps the proof, ledger, registration
-- and audit record in sync. A screenshot is approved only after the organiser
-- independently verifies the matching amount/reference in their bank or UPI.
CREATE OR REPLACE FUNCTION public.review_payment_proof(
  p_proof_id uuid,
  p_reviewer_id uuid,
  p_decision text,
  p_note text DEFAULT NULL
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
BEGIN
  IF p_decision IS NULL OR p_decision NOT IN ('approved', 'rejected') THEN
    RAISE EXCEPTION 'Decision must be approved or rejected';
  END IF;
  IF p_decision = 'rejected' AND length(btrim(COALESCE(p_note, ''))) < 5 THEN
    RAISE EXCEPTION 'A rejection needs a reason of at least 5 characters';
  END IF;
  SELECT * INTO v_proof FROM public.payment_proofs
  WHERE id = p_proof_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Payment proof % does not exist', p_proof_id;
  END IF;
  SELECT * INTO v_registration FROM public.registrations
  WHERE id = v_proof.registration_id FOR UPDATE;
  IF NOT FOUND OR v_registration.tournament_id <> v_proof.tournament_id THEN
    RAISE EXCEPTION 'Payment proof is not linked to a valid registration';
  END IF;
  SELECT * INTO v_tournament FROM public.tournaments
  WHERE id = v_proof.tournament_id;
  IF NOT EXISTS (
    SELECT 1 FROM public.profiles p WHERE p.id = p_reviewer_id
      AND p.role = 'admin'
      AND (
        v_tournament.owner_id = p_reviewer_id OR EXISTS (
          SELECT 1 FROM public.tournament_access a
          WHERE a.tournament_id = v_proof.tournament_id
            AND a.user_id = p_reviewer_id AND a.status = 'approved'
            AND a.access_role = 'manager'
        )
      )
  ) THEN
    RAISE EXCEPTION 'Only this tournament owner or an approved manager may review payment proof';
  END IF;

  IF v_proof.status <> 'pending' THEN
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
        'proof_id', v_proof.id, 'reviewed_by', p_reviewer_id)
    ) RETURNING * INTO v_payment;
    v_payment_json := to_jsonb(v_payment);
    UPDATE public.registrations SET payment_status = 'paid'
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
    user_id, action, entity_type, entity_id, previous_state, new_state
  ) VALUES (
    p_reviewer_id, 'payment.proof_' || p_decision, 'payment_proof',
    p_proof_id::text, v_before, to_jsonb(v_proof)
  );
  RETURN jsonb_build_object('proof', to_jsonb(v_proof),
    'payment', v_payment_json, 'registration', to_jsonb(v_registration));
END;
$$;

REVOKE ALL ON FUNCTION public.review_payment_proof(
  uuid, uuid, text, text
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.review_payment_proof(
  uuid, uuid, text, text
) TO service_role;
