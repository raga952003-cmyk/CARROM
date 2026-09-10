-- =============================================================================
-- 015 — Entry fees: what was charged, what was paid, and by whom
--
-- `tournaments.entry_fee` has existed since the base schema, and the
-- registration form has always shown it, but nothing ever collected it: the
-- fee was a number on a poster, settled in cash at the venue, and the badge on
-- the form said "On-Site / UPI Verified" because no money passed through the
-- application at all. `registrations.payment_status` could say 'paid', but only
-- an organiser ticking it by hand ever set it.
--
-- This adds the ledger that makes an online entry fee real. One row per
-- attempt, keyed by Razorpay's own order id, holding what was asked for and
-- what actually arrived. The registration keeps its own `payment_status` as
-- the summary; this table is the evidence behind it.
--
-- Three properties this table exists to guarantee:
--
--   1. The amount is a fact, recorded server-side before the player is sent to
--      checkout, so a disputed charge can be answered from the database rather
--      than from what the browser claimed it was paying.
--   2. `razorpay_payment_id` is UNIQUE, which is what makes the webhook safe to
--      retry. Razorpay redelivers, and the browser callback can arrive for the
--      same payment as well; both paths write through this constraint, so a
--      duplicate is a no-op rather than a second confirmed entry.
--   3. `signature_verified` is stored, not assumed. A row that reached 'paid'
--      without a verified signature is a bug, and this makes it findable.
--
-- Amounts are in PAISE, as integers, because that is the only unit Razorpay
-- accepts and because NUMERIC rounding on a currency total is how you end up
-- one rupee short across a hundred entries. `entry_fee` stays NUMERIC rupees on
-- the tournament -- that is what an organiser types -- and the conversion
-- happens once, in the API, when the order is created.
--
-- Safe to re-run.
-- =============================================================================

DO $$
BEGIN
    IF to_regclass('public.registrations') IS NULL OR to_regclass('public.tournaments') IS NULL THEN
        RAISE EXCEPTION
            'Base schema missing in this database (current_database=%). '
            'Run db/schema.sql first, or switch to the project whose ref '
            'matches SUPABASE_URL in backend/.env.', current_database();
    END IF;
END $$;

-- -----------------------------------------------------------------------------
-- 1. The payments ledger
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    registration_id UUID NOT NULL REFERENCES public.registrations(id) ON DELETE CASCADE,
    -- Denormalised so an organiser can total a tournament's takings without
    -- joining through registrations, and so the row still says which event it
    -- belonged to while a deletion cascade is in flight.
    tournament_id UUID NOT NULL REFERENCES public.tournaments(id) ON DELETE CASCADE,

    -- Razorpay's identifiers. The order is created by us; the payment id
    -- arrives only once money has actually moved.
    razorpay_order_id TEXT NOT NULL,
    razorpay_payment_id TEXT,

    amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
    currency TEXT NOT NULL DEFAULT 'INR',

    -- created  : order opened, player sent to checkout, nothing paid yet
    -- paid     : signature verified, money captured
    -- failed   : Razorpay reported the attempt failed
    -- refunded : reversed after the fact
    status TEXT NOT NULL DEFAULT 'created'
        CHECK (status IN ('created', 'paid', 'failed', 'refunded')),

    -- Whether the HMAC on the callback (or webhook) checked out. Kept as a
    -- column rather than inferred from status so that "we took the money" and
    -- "we proved it was really Razorpay telling us so" stay separable.
    signature_verified BOOLEAN NOT NULL DEFAULT false,

    -- Which path confirmed it: 'callback' (the browser came back) or
    -- 'webhook' (Razorpay told us directly). Useful when reconciling a day's
    -- takings against the dashboard, and the only way to notice that callbacks
    -- are working while webhooks are not.
    confirmed_via TEXT CHECK (confirmed_via IN ('callback', 'webhook')),

    -- Razorpay's own failure description, kept verbatim for support.
    error_description TEXT,

    method TEXT,
    notes JSONB,

    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
    paid_at TIMESTAMPTZ
);

-- -----------------------------------------------------------------------------
-- 2. The uniqueness that makes retries safe
--
-- Both constraints are the reason this design does not need a lock. The order
-- id is unique because one order belongs to one registration attempt; the
-- payment id is unique because one payment confirms one entry, no matter how
-- many times Razorpay tells us about it.
--
-- The payment id index is PARTIAL: rows sit at NULL between order creation and
-- payment, and a plain UNIQUE would allow only one unpaid order in the entire
-- table on databases that treat NULLs as equal. It is written this way so the
-- intent survives someone reading it later.
-- -----------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS uniq_payments_order
    ON public.payments(razorpay_order_id);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_payments_payment
    ON public.payments(razorpay_payment_id)
    WHERE razorpay_payment_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_payments_registration
    ON public.payments(registration_id, status);

CREATE INDEX IF NOT EXISTS idx_payments_tournament
    ON public.payments(tournament_id, status);

-- -----------------------------------------------------------------------------
-- 3. Row level security
--
-- The API writes through the service-role client, which bypasses all of this.
-- These policies exist for the anon key that ships in the browser bundle --
-- migration 011's header records that key being lifted out of the live bundle
-- and used to read a table directly, so every new table gets locked down on
-- the way in rather than after the same lesson twice.
--
-- A player may READ their own payments, so a receipt can be shown without a
-- round trip through the API. Nobody may write through PostgREST at all: an
-- INSERT here is a claim that money arrived, and only the server, holding the
-- key secret and having checked a signature, is allowed to make it.
-- -----------------------------------------------------------------------------
ALTER TABLE public.payments ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS select_payments_self ON public.payments;
CREATE POLICY select_payments_self ON public.payments
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.registrations r
            LEFT JOIN public.teams t ON t.id = r.team_id
            WHERE r.id = payments.registration_id
              AND (
                  r.player_id = auth.uid()
                  OR t.player1_id = auth.uid()
                  OR t.player2_id = auth.uid()
              )
        )
    );

-- No INSERT, UPDATE or DELETE policy is defined, so with RLS enabled every
-- write through the anon or authenticated role is refused. Stated explicitly
-- because "there is no policy" and "somebody deleted the policy" look
-- identical in a schema dump.
REVOKE INSERT, UPDATE, DELETE ON public.payments FROM anon, authenticated;

-- -----------------------------------------------------------------------------
-- 4. Registrations gain a fee snapshot
--
-- The fee is copied onto the registration at the moment of entry. An organiser
-- who raises the fee halfway through a registration window must not thereby
-- change what an already-entered player owes, and a receipt has to be able to
-- say what THIS entry cost -- which `tournaments.entry_fee` stops being able to
-- answer the moment it is edited.
-- -----------------------------------------------------------------------------
ALTER TABLE public.registrations
    ADD COLUMN IF NOT EXISTS fee_paise INTEGER CHECK (fee_paise IS NULL OR fee_paise >= 0);

DO $$
BEGIN
    RAISE NOTICE 'Migration 015 applied: payments ledger created, registrations.fee_paise added. Entry fees can now be collected through Razorpay.';
END $$;
