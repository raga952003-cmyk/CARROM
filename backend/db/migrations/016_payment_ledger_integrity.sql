-- =============================================================================
-- 016 — Payment ledger integrity: one paid entry, and a ledger that survives
--
-- `_settle_payment` already refuses to settle a payment against a registration
-- that another payment has paid (routers/payments.py, "Another payment has
-- already settled this entry"). That check is a SELECT followed by an UPDATE,
-- and nothing stands between them.
--
-- The window is small and entirely reachable. One entry can have several live
-- orders at Razorpay at once -- a superseded order after a fee correction, a
-- replacement after a failed attempt, or two opened by a double-tapped Pay
-- button -- and every one of them stays payable until it expires. The browser
-- callback and the webhook for two different payments can therefore arrive at
-- the same moment, on two serverless instances that cannot see each other's
-- in-flight work. Both SELECT, both find no paid sibling, both UPDATE. The
-- player is charged twice and the application believes neither is a duplicate.
--
-- On Vercel this is not theoretical: each request may be a separate instance,
-- so there is no process-local lock to fall back on. The only place the
-- invariant can be enforced is the database.
--
-- After this, the loser of that race fails on a constraint violation instead
-- of double-confirming. The application check stays -- it produces the good
-- error message and the audit record; this is the backstop underneath it.
--
-- Safe to re-run.
--
-- IF THIS MIGRATION FAILS it is because the data already violates it: some
-- registration already has two rows marked 'paid'. That is a real double
-- charge that needs refunding, not a migration to force through. The DO block
-- below names them before the index is attempted, so they appear in the
-- output rather than only in the error.
-- =============================================================================

DO $$
DECLARE
    offenders INTEGER;
BEGIN
    IF to_regclass('public.payments') IS NULL THEN
        RAISE NOTICE 'public.payments is not present; apply 015_payments.sql first. Nothing to do.';
        RETURN;
    END IF;

    SELECT count(*) INTO offenders FROM (
        SELECT registration_id
        FROM public.payments
        WHERE status = 'paid'
        GROUP BY registration_id
        HAVING count(*) > 1
    ) AS dupes;

    IF offenders > 0 THEN
        RAISE WARNING 'Cannot enforce one-paid-per-entry: % registration(s) already hold more than one paid payment. These are double charges. List them with: SELECT registration_id, count(*) FROM public.payments WHERE status = ''paid'' GROUP BY registration_id HAVING count(*) > 1; refund the extras in the Razorpay dashboard and set their status to ''refunded'', then re-run this migration.', offenders;
    ELSE
        RAISE NOTICE 'No duplicate paid payments found; enforcing the constraint.';
    END IF;
END $$;

-- Partial, so it constrains only what matters. A registration may accumulate
-- any number of 'created', 'failed' or 'refunded' rows -- those are the
-- history of attempts, and the history is the point of the ledger. It is
-- exactly one row in 'paid' that must be true at a time.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_payments_one_paid_per_registration
    ON public.payments (registration_id)
    WHERE status = 'paid';

DO $$
BEGIN
    RAISE NOTICE 'Migration 016 applied: a registration can now hold at most one paid payment, enforced by uniq_payments_one_paid_per_registration.';
END $$;


-- -----------------------------------------------------------------------------
-- 2. A settled payment outlives the rows that point at it
--
-- Both foreign keys on public.payments are ON DELETE CASCADE, and both parents
-- have live delete paths in the API:
--
--   DELETE /api/tournaments/{id}   (routers/tournaments.py)
--   DELETE /api/players/{id}       (routers/players.py, via the registration)
--
-- So an organiser tidying up an old event silently destroys the record of
-- every rupee it took. 016_drop_payments -- the migration this project wrote
-- when the integration was removed, and which has since been deleted -- said
-- it plainly: the payments table "is the only record on our side" linking a
-- Razorpay payment to the entry it paid for. Razorpay keeps its own record,
-- but not the link back to which entry was paid.
--
-- CASCADE is left in place for the rows that are merely noise -- an abandoned
-- 'created' order, a 'failed' attempt -- because deleting a tournament that
-- nobody paid for should stay easy. What is refused is deleting anything that
-- still has money recorded against it: the organiser has to deal with that
-- deliberately, by refunding and marking the row, or by exporting it first.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.refuse_delete_with_settled_payments()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    settled INTEGER;
BEGIN
    IF TG_TABLE_NAME = 'tournaments' THEN
        SELECT count(*) INTO settled FROM public.payments
        WHERE tournament_id = OLD.id AND status IN ('paid', 'refunded');
    ELSE
        SELECT count(*) INTO settled FROM public.payments
        WHERE registration_id = OLD.id AND status IN ('paid', 'refunded');
    END IF;

    IF settled > 0 THEN
        RAISE EXCEPTION
            'Refusing to delete this % : % settled payment(s) are recorded against it, and deleting it would destroy the only record linking that money to what it paid for. Refund and mark them ''refunded'' in the Razorpay dashboard and here, or export public.payments first.',
            TG_TABLE_NAME, settled
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS trg_tournaments_keep_settled_payments ON public.tournaments;
CREATE TRIGGER trg_tournaments_keep_settled_payments
    BEFORE DELETE ON public.tournaments
    FOR EACH ROW EXECUTE FUNCTION public.refuse_delete_with_settled_payments();

DROP TRIGGER IF EXISTS trg_registrations_keep_settled_payments ON public.registrations;
CREATE TRIGGER trg_registrations_keep_settled_payments
    BEFORE DELETE ON public.registrations
    FOR EACH ROW EXECUTE FUNCTION public.refuse_delete_with_settled_payments();


-- -----------------------------------------------------------------------------
-- 3. Say what the browser roles may do, rather than inheriting it
--
-- 015 revokes INSERT, UPDATE and DELETE but issues no GRANT, so every
-- remaining privilege on public.payments comes from Supabase's project-level
-- ALTER DEFAULT PRIVILEGES ... GRANT ALL ON TABLES TO anon, authenticated.
-- GRANT ALL includes TRUNCATE, which the revoke above does not cover and
-- which RLS does not restrain -- a TRUNCATE is not a DELETE and no row-level
-- policy is consulted for it.
--
-- SELECT stays for authenticated, because the RLS policy 015 defines is what
-- scopes a player to their own payments; without the grant that policy would
-- have nothing to narrow.
-- -----------------------------------------------------------------------------
REVOKE ALL ON public.payments FROM anon, authenticated;
GRANT SELECT ON public.payments TO authenticated;

DO $$
BEGIN
    RAISE NOTICE 'Migration 016 part 2/3 applied: settled payments now block deletion of their tournament or registration.';
    RAISE NOTICE 'Migration 016 part 3/3 applied: privileges on public.payments stated explicitly; TRUNCATE revoked from anon and authenticated.';
END $$;
