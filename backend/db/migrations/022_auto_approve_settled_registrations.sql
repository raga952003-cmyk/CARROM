-- Apply after 021. A verified payment or an organiser fee waiver confirms the
-- registration in the SAME write that settles its payment status. This covers
-- GPay proof reviews, desk-recorded payments, and waivers without a second
-- "Approve entry" click. Razorpay already writes both fields together.

CREATE OR REPLACE FUNCTION public.auto_approve_settled_registration()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
BEGIN
  IF NEW.payment_status NOT IN ('paid', 'waived')
     OR OLD.payment_status IS NOT DISTINCT FROM NEW.payment_status THEN
    RETURN NEW;
  END IF;

  -- Even an application update that sets both fields must not revive a
  -- rejected entry or admit one after its tournament has finished.
  IF OLD.status = 'rejected' OR NOT EXISTS (
    SELECT 1 FROM public.tournaments t WHERE t.id = NEW.tournament_id
      AND t.status NOT IN ('cancelled', 'completed')
  ) THEN
    IF OLD.status IN ('pending', 'rejected') THEN
      NEW.status := OLD.status;
    END IF;
    RETURN NEW;
  END IF;

  -- A stray payment flag cannot admit someone without a paid ledger row.
  IF NEW.payment_status = 'paid' AND NOT EXISTS (
    SELECT 1 FROM public.payments p
    WHERE p.registration_id = NEW.id AND p.status = 'paid'
  ) THEN
    IF OLD.status = 'pending' THEN
      NEW.status := 'pending';
    END IF;
    RETURN NEW;
  END IF;

  IF NEW.status = 'pending' THEN
    NEW.status := 'approved';
  END IF;
  RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.auto_approve_settled_registration()
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.auto_approve_settled_registration()
  TO service_role;

DROP TRIGGER IF EXISTS auto_approve_settled_registration
  ON public.registrations;
CREATE TRIGGER auto_approve_settled_registration
  BEFORE UPDATE OF payment_status ON public.registrations
  FOR EACH ROW EXECUTE FUNCTION public.auto_approve_settled_registration();

-- A read-only readiness check lets the API verify that the trigger is wired
-- to the intended function; merely seeing the function is not enough.
CREATE OR REPLACE FUNCTION public.registration_auto_approval_ready()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
  SELECT EXISTS (
    SELECT 1 FROM pg_catalog.pg_trigger t
    WHERE t.tgrelid = 'public.registrations'::pg_catalog.regclass
      AND t.tgname = 'auto_approve_settled_registration'
      AND t.tgfoid =
        'public.auto_approve_settled_registration()'::pg_catalog.regprocedure
      AND t.tgenabled <> 'D'
  );
$$;
REVOKE ALL ON FUNCTION public.registration_auto_approval_ready()
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.registration_auto_approval_ready()
  TO service_role;

-- Repair older paid entries left pending by the two-click admin workflow.
-- Free entries are deliberately excluded: their admission is still an
-- organiser decision. Only payments with a settled ledger row, or fee waivers
-- with a recorded organiser audit, qualify.
DO $$
DECLARE
  v_before public.registrations%ROWTYPE;
  v_after public.registrations%ROWTYPE;
BEGIN
  FOR v_before IN
    SELECT r.* FROM public.registrations r
    JOIN public.tournaments t ON t.id = r.tournament_id
    WHERE r.status = 'pending'
      AND t.status NOT IN ('cancelled', 'completed')
      AND (
        (r.payment_status = 'paid' AND EXISTS (
          SELECT 1 FROM public.payments p
          WHERE p.registration_id = r.id AND p.status = 'paid'
        ))
        OR (r.payment_status = 'waived' AND EXISTS (
          SELECT 1 FROM public.audit_logs a
          WHERE a.entity_type = 'registration'
            AND a.entity_id = r.id::text
            AND a.action = 'payment.fee_waived'
        ))
      )
    FOR UPDATE OF r
  LOOP
    UPDATE public.registrations SET status = 'approved'
    WHERE id = v_before.id AND status = 'pending'
    RETURNING * INTO v_after;
    IF FOUND THEN
      INSERT INTO public.audit_logs (
        user_id, action, entity_type, entity_id, previous_state,
        new_state, request_context
      ) VALUES (
        NULL, 'registration.auto_approved_after_payment',
        'registration', v_before.id::text,
        to_jsonb(v_before), to_jsonb(v_after),
        jsonb_build_object('migration', '022_auto_approve_settled_registrations')
      );
    END IF;
  END LOOP;
END;
$$;
