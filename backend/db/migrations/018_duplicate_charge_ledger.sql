-- Retain captured duplicate charges so an organiser can refund and reconcile them.
ALTER TABLE public.payments DROP CONSTRAINT IF EXISTS payments_status_check;
ALTER TABLE public.payments ADD CONSTRAINT payments_status_check
  CHECK (status IN ('created', 'paid', 'failed', 'refunded', 'refund_due'));
CREATE INDEX IF NOT EXISTS idx_payments_refund_due
  ON public.payments(tournament_id, created_at) WHERE status = 'refund_due';

-- A captured duplicate is still money in transit. Keep its ledger row when
-- someone tries to remove the tournament or registration before reconciling it.
CREATE OR REPLACE FUNCTION public.refuse_delete_with_settled_payments()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  settled integer;
BEGIN
  IF TG_TABLE_NAME = 'tournaments' THEN
    SELECT count(*) INTO settled FROM public.payments
    WHERE tournament_id = OLD.id AND status IN ('paid', 'refunded', 'refund_due');
  ELSE
    SELECT count(*) INTO settled FROM public.payments
    WHERE registration_id = OLD.id AND status IN ('paid', 'refunded', 'refund_due');
  END IF;

  IF settled > 0 THEN
    RAISE EXCEPTION
      'Cannot delete % with % captured or refunded payment records; reconcile and retain the ledger.',
      TG_TABLE_NAME, settled
      USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN OLD;
END;
$$;
