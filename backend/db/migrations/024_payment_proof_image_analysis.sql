-- Visual reuse and OCR triage for uploaded payment receipts. The result is
-- evidence for review, never an automatic assertion that funds were received.
ALTER TABLE public.payment_proofs
  ADD COLUMN IF NOT EXISTS image_dhash text
    CHECK (image_dhash IS NULL OR image_dhash ~ '^[0-9a-f]{16}$'),
  ADD COLUMN IF NOT EXISTS image_analysis jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_payment_proofs_image_dhash
  ON public.payment_proofs(image_dhash) WHERE image_dhash IS NOT NULL;

CREATE OR REPLACE FUNCTION public.payment_proof_similar_images(
  p_hash text,
  p_max_distance integer DEFAULT 4
)
RETURNS TABLE(proof_id uuid, registration_id uuid, transaction_reference text, distance integer)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
  candidate record;
  index_no integer;
  nibble integer;
  score integer;
  returned integer := 0;
BEGIN
  IF p_hash !~ '^[0-9a-f]{16}$' OR p_max_distance NOT BETWEEN 0 AND 8 THEN
    RAISE EXCEPTION 'Invalid receipt image fingerprint';
  END IF;
  FOR candidate IN
    SELECT p.id, p.registration_id, p.transaction_reference, p.image_dhash
    FROM public.payment_proofs p
    WHERE p.image_dhash IS NOT NULL
    ORDER BY p.submitted_at DESC
  LOOP
    score := 0;
    FOR index_no IN 1..16 LOOP
      nibble := (strpos('0123456789abcdef', substr(candidate.image_dhash, index_no, 1)) - 1)
                # (strpos('0123456789abcdef', substr(p_hash, index_no, 1)) - 1);
      score := score + (nibble & 1) + ((nibble >> 1) & 1)
                     + ((nibble >> 2) & 1) + ((nibble >> 3) & 1);
      EXIT WHEN score > p_max_distance;
    END LOOP;
    IF score <= p_max_distance THEN
      proof_id := candidate.id;
      registration_id := candidate.registration_id;
      transaction_reference := candidate.transaction_reference;
      distance := score;
      RETURN NEXT;
      returned := returned + 1;
      EXIT WHEN returned >= 5;
    END IF;
  END LOOP;
END;
$$;

REVOKE ALL ON FUNCTION public.payment_proof_similar_images(text, integer)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.payment_proof_similar_images(text, integer)
  TO service_role;
