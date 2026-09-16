-- =============================================================================
-- 016 — Remove the payments ledger
--
-- The Razorpay entry-fee integration has been taken out of the application, so
-- the table and column migration 015 added have nothing reading or writing
-- them any more. This drops both.
--
-- OPTIONAL. An unused table costs nothing but a line in the schema, and
-- dropping it is the only irreversible step in taking the integration out --
-- everything else is a git revert away. If there is any chance of putting
-- entry-fee collection back, or of a payment record being wanted for
-- reconciliation, leave this unapplied and the data stays where it is.
--
-- WHAT THIS DELETES, stated plainly rather than discovered afterwards:
--
--   * public.payments, and every row in it. Each row is the record of one
--     payment attempt -- the Razorpay order and payment ids, the amount, and
--     whether the signature verified. If any real money ever went through this
--     application, THIS TABLE IS THE ONLY RECORD OF IT on our side. Razorpay's
--     dashboard keeps its own, but the link back to which entry a payment was
--     for lives only here.
--   * registrations.fee_paise, the per-entry fee snapshot.
--
-- So: check before running it. This is enough --
--
--     SELECT count(*) FROM public.payments;
--
-- and if that is not 0, decide deliberately. Export the table first if you
-- want the history:
--
--     SELECT * FROM public.payments;      -- then download the result
--
-- NOT dropped: registrations.payment_status and tournaments.entry_fee. Both
-- predate the Razorpay work and are still used -- the fee is shown on the
-- poster and the registration form, and an organiser still marks an entry paid
-- or waived by hand once it is settled at the venue.
--
-- Safe to re-run.
-- =============================================================================

DO $$
BEGIN
    IF to_regclass('public.payments') IS NOT NULL THEN
        RAISE NOTICE 'Dropping public.payments, which currently holds % row(s).',
            (SELECT count(*) FROM public.payments);
    ELSE
        RAISE NOTICE 'public.payments is not present; nothing to drop.';
    END IF;
END $$;

DROP TABLE IF EXISTS public.payments;

ALTER TABLE public.registrations DROP COLUMN IF EXISTS fee_paise;

DO $$
BEGIN
    RAISE NOTICE 'Migration 016 applied: payments ledger and registrations.fee_paise removed. Entry fees are settled at the venue and recorded by hand in registrations.payment_status.';
END $$;
