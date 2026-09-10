-- =============================================================================
-- Carrom Arena — pending migrations, combined
--
-- Everything the database may still be missing, in one paste. All three parts
-- are idempotent: running this twice is harmless, and running it when some of
-- it is already applied is harmless too.
--
--   013  the profiles trigger, and row level security across every table
--   014  nobody grants themselves a role
--   015  the payments ledger, so entry fees can be collected through Razorpay
--
-- THE ORDER MATTERS. 013 creates the `update_profiles_self` policy and 014
-- replaces it with a narrower one. Applying 013 after 014 puts the permissive
-- version back and undoes the fix -- silently, because both succeed. Paste the
-- whole file, in this order, rather than picking parts out of it.
--
-- Why these three and not the others: 004-012 are all visible to /api/health,
-- which reports them applied. 013 and 014 leave nothing PostgREST can see, so
-- health cannot tell whether they have been run -- they are included here
-- because re-running them costs nothing and being wrong about them is a
-- privilege escalation. 015 is genuinely pending; health says so.
--
-- Paste the whole file into the Supabase SQL editor and run it once. Each part
-- prints a RAISE NOTICE, so the output should end with three notices. Then
-- GET /api/health should report  "migrations": "all applied".
-- =============================================================================

-- ==========================================================================
-- 013_profiles_trigger_and_rls.sql
-- ==========================================================================

-- =============================================================================
-- 013 — The sign-up trigger and row-level security, as a numbered migration
--
-- Everything here used to live in db/triggers_and_security.sql, a file outside
-- the numbered sequence. That is how it got skipped: a fresh project had
-- schema.sql and every migration applied and still no handle_new_user trigger,
-- so a sign-up created an auth user and no profiles row, and the next write
-- that expected the row found nothing to update. The application grew
-- fallbacks for the missing row (routers/auth.py, routers/players.py), but the
-- trigger is what is supposed to be there.
--
-- Same trigger, same RLS switches, same policies, in the form the rest of the
-- migrations take: idempotent, guarded, and announcing itself at the end. The
-- original file stays in the repository for reference and must not be run --
-- see the note at its top.
--
-- One deliberate difference. The original's select_profiles policy read every
-- column of every profile to anyone holding the anon key, which migration 011
-- closed by replacing it with select_own_profile and a public_profiles view.
-- Re-running the original after 011 would quietly reopen that. This file
-- carries 011's policy, so it is correct whichever order the two run in.
--
-- The health probe cannot see any of this -- a trigger on auth.users and RLS
-- policies are invisible through PostgREST -- so /api/health lists 013 under
-- unprobeable_migrations rather than claiming it applied. Check for the NOTICE
-- below in the SQL editor's output instead.
--
-- Safe to re-run.
-- =============================================================================

DO $$
BEGIN
    IF to_regclass('public.profiles') IS NULL OR to_regclass('public.matches') IS NULL THEN
        RAISE EXCEPTION
            'Base schema missing in this database (current_database=%). '
            'Run db/schema.sql first, or switch to the project whose ref '
            'matches SUPABASE_URL in backend/.env.', current_database();
    END IF;
END $$;

-- -----------------------------------------------------------------------------
-- 1. Sync user registration from Supabase Auth to profiles
--
-- Copies only what the sign-up form carries. The role is read from
-- app_metadata first because that is the half the user cannot edit; the
-- user_metadata fallback is for accounts created before roles were stamped
-- there.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger AS $$
BEGIN
  INSERT INTO public.profiles (id, name, email, role, rating)
  VALUES (
    new.id,
    COALESCE(new.raw_user_meta_data->>'name', 'User'),
    new.email,
    COALESCE(new.raw_app_meta_data->>'role', new.raw_user_meta_data->>'role', 'player'),
    COALESCE((new.raw_user_meta_data->>'rating')::integer, 1500)
  );
  RETURN new;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- -----------------------------------------------------------------------------
-- 2. Row-level security
--
-- ENABLE ROW LEVEL SECURITY is idempotent on its own.
-- -----------------------------------------------------------------------------
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tournaments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.teams ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.registrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.matches ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.boards ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.score_audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY;

-- Whether the active user is an admin, read from the JWT's app_metadata --
-- the half of the token the user cannot edit.
CREATE OR REPLACE FUNCTION public.is_admin()
RETURNS BOOLEAN AS $$
  SELECT COALESCE(auth.jwt() -> 'app_metadata' ->> 'role' = 'admin', false);
$$ LANGUAGE sql SECURITY DEFINER;

-- -----------------------------------------------------------------------------
-- 3. Policies
--
-- CREATE POLICY has no IF NOT EXISTS, so each one is dropped first; that is
-- what makes a second run of this file harmless.
-- -----------------------------------------------------------------------------

-- Profiles. select_profiles is the blanket public read that 011 closed; it is
-- dropped here as well so that running this file can never bring it back.
DROP POLICY IF EXISTS select_profiles ON public.profiles;
DROP POLICY IF EXISTS select_own_profile ON public.profiles;
DROP POLICY IF EXISTS update_profiles_self ON public.profiles;
DROP POLICY IF EXISTS admin_all_profiles ON public.profiles;

CREATE POLICY select_own_profile ON public.profiles FOR SELECT USING (auth.uid() = id);
CREATE POLICY update_profiles_self ON public.profiles FOR UPDATE USING (auth.uid() = id);
CREATE POLICY admin_all_profiles ON public.profiles FOR ALL USING (public.is_admin());

-- Tournaments
DROP POLICY IF EXISTS select_tournaments ON public.tournaments;
DROP POLICY IF EXISTS admin_all_tournaments ON public.tournaments;

CREATE POLICY select_tournaments ON public.tournaments FOR SELECT TO public USING (true);
CREATE POLICY admin_all_tournaments ON public.tournaments FOR ALL USING (public.is_admin());

-- Teams
DROP POLICY IF EXISTS select_teams ON public.teams;
DROP POLICY IF EXISTS insert_teams_member ON public.teams;
DROP POLICY IF EXISTS admin_all_teams ON public.teams;

CREATE POLICY select_teams ON public.teams FOR SELECT TO public USING (true);
CREATE POLICY insert_teams_member ON public.teams FOR INSERT WITH CHECK (auth.uid() = player1_id OR auth.uid() = player2_id);
CREATE POLICY admin_all_teams ON public.teams FOR ALL USING (public.is_admin());

-- Registrations
DROP POLICY IF EXISTS select_registrations ON public.registrations;
DROP POLICY IF EXISTS insert_registrations_self ON public.registrations;
DROP POLICY IF EXISTS admin_all_registrations ON public.registrations;

CREATE POLICY select_registrations ON public.registrations FOR SELECT TO public USING (true);
CREATE POLICY insert_registrations_self ON public.registrations FOR INSERT WITH CHECK (
  auth.uid() = player_id OR
  EXISTS (SELECT 1 FROM public.teams WHERE id = team_id AND (player1_id = auth.uid() OR player2_id = auth.uid()))
);
CREATE POLICY admin_all_registrations ON public.registrations FOR ALL USING (public.is_admin());

-- Matches and boards
DROP POLICY IF EXISTS select_matches ON public.matches;
DROP POLICY IF EXISTS admin_all_matches ON public.matches;

CREATE POLICY select_matches ON public.matches FOR SELECT TO public USING (true);
CREATE POLICY admin_all_matches ON public.matches FOR ALL USING (public.is_admin());

DROP POLICY IF EXISTS select_boards ON public.boards;
DROP POLICY IF EXISTS admin_all_boards ON public.boards;

CREATE POLICY select_boards ON public.boards FOR SELECT TO public USING (true);
CREATE POLICY admin_all_boards ON public.boards FOR ALL USING (public.is_admin());

-- Score audit logs
DROP POLICY IF EXISTS select_score_audit ON public.score_audit_logs;
DROP POLICY IF EXISTS admin_insert_score_audit ON public.score_audit_logs;

CREATE POLICY select_score_audit ON public.score_audit_logs FOR SELECT TO public USING (true);
CREATE POLICY admin_insert_score_audit ON public.score_audit_logs FOR INSERT WITH CHECK (public.is_admin());

-- Notifications
DROP POLICY IF EXISTS select_notifications ON public.notifications;
DROP POLICY IF EXISTS update_own_notifications ON public.notifications;
DROP POLICY IF EXISTS admin_all_notifications ON public.notifications;

CREATE POLICY select_notifications ON public.notifications FOR SELECT USING (profile_id IS NULL OR profile_id = auth.uid());
-- Without this, only admins could ever flip `read`, so "mark as read" silently
-- updated zero rows for every player.
CREATE POLICY update_own_notifications ON public.notifications FOR UPDATE
  USING (profile_id = auth.uid())
  WITH CHECK (profile_id = auth.uid());
CREATE POLICY admin_all_notifications ON public.notifications FOR ALL USING (public.is_admin());

-- Administrative audit logs
DROP POLICY IF EXISTS admin_select_audit_logs ON public.audit_logs;

CREATE POLICY admin_select_audit_logs ON public.audit_logs FOR SELECT USING (public.is_admin());

DO $$
BEGIN
    RAISE NOTICE 'Migration 013 applied: sign-ups create a profile row, and row-level security is enforced on every table.';
END $$;


-- ==========================================================================
-- 014_lock_profile_role.sql
-- ==========================================================================

-- =============================================================================
-- 014 — Nobody grants themselves a role
--
-- 013 gave everyone the right to edit their own profile:
--
--     CREATE POLICY update_profiles_self ON public.profiles
--       FOR UPDATE USING (auth.uid() = id);
--
-- which is right for a name, a club and a phone number, and wrong for the one
-- column on that row the API treats as authority. `profiles.role` is what
-- verify_admin reads, through the service client, bypassing RLS. The policy
-- has no WITH CHECK and no column list, and nothing revokes UPDATE(role) from
-- `authenticated`, so the row a player is allowed to write includes the field
-- that decides whether they are an administrator.
--
-- The anon key ships in the browser bundle — migration 011's own header records
-- it being lifted out of the live bundle and used to read the table — so this
-- needs no access to the application at all. A signed-in player with their own
-- token can send:
--
--     PATCH /rest/v1/profiles?id=eq.<their own id>     {"role": "admin"}
--
-- The row still satisfies auth.uid() = id, so the policy accepts it, and from
-- the next request onwards every admin-only endpoint agrees they are one.
--
-- Two locks, because either alone is a single point of failure. The REVOKE
-- stops the column being named in an UPDATE at all; the trigger refuses the
-- change even if a future policy or grant hands the column back. The trigger
-- is the one that survives somebody re-running an older file.
--
-- The service role is untouched. It is what the API writes through when an
-- organiser genuinely promotes somebody, and what db/promote_admin.py uses.
-- =============================================================================

-- 1. The column cannot be written by a browser-held key.
REVOKE UPDATE (role) ON public.profiles FROM anon, authenticated;

-- 2. And cannot be changed even if it could be written.
--
-- SECURITY DEFINER so the check runs regardless of the caller. The service
-- role -- and only it -- is allowed through, which is how a real promotion
-- lands: the API and promote_admin.py both write with the service key.
CREATE OR REPLACE FUNCTION public.guard_profile_role()
RETURNS trigger AS $$
BEGIN
    IF NEW.role IS DISTINCT FROM OLD.role
       AND coalesce(current_setting('request.jwt.claim.role', true), '') <> 'service_role'
       AND current_user <> 'service_role'
    THEN
        RAISE EXCEPTION
            'profiles.role may only be changed by an organiser (service role); '
            'use db/promote_admin.py or the admin API.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS guard_profile_role ON public.profiles;
CREATE TRIGGER guard_profile_role
    BEFORE UPDATE ON public.profiles
    FOR EACH ROW EXECUTE FUNCTION public.guard_profile_role();

-- 3. Say plainly what the self-update policy is now for.
--
-- Recreated rather than left as it was, so reading 013 and this file together
-- does not leave the impression that the policy is what limits the columns.
-- It does not: the REVOKE and the trigger above do.
DROP POLICY IF EXISTS update_profiles_self ON public.profiles;
CREATE POLICY update_profiles_self ON public.profiles
    FOR UPDATE
    USING (auth.uid() = id)
    WITH CHECK (auth.uid() = id);

DO $$
BEGIN
    RAISE NOTICE 'Migration 014 applied: profiles.role can no longer be set by the account holder.';
END $$;


-- ==========================================================================
-- 015_payments.sql
-- ==========================================================================

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


