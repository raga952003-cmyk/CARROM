-- =============================================================================
-- Carrom Arena — pending migrations, combined
--
-- Everything the database may still be missing, in one paste. Both parts are
-- idempotent: running this twice is harmless, and running it when one of them
-- is already applied is harmless too.
--
--   013  the profiles trigger, and row level security across every table
--   014  nobody grants themselves a role
--
-- THE ORDER MATTERS. 013 creates the `update_profiles_self` policy and 014
-- replaces it with a narrower one. Applying 013 after 014 puts the permissive
-- version back and undoes the fix -- silently, because both succeed. Paste the
-- whole file, in this order, rather than picking parts out of it.
--
-- Why these two and not the others: 004-012 are all visible to /api/health,
-- which reports them applied. 013 and 014 leave nothing PostgREST can see, so
-- health cannot tell whether they have been run -- they are included here
-- because re-running them costs nothing and being wrong about them is a
-- privilege escalation.
--
-- 015 (the payments ledger) was removed along with the Razorpay integration.
-- A database that already ran it can be cleaned up with 016_drop_payments.sql,
-- which is optional -- an unused table is harmless.
--
-- Paste the whole file into the Supabase SQL editor and run it once. Each part
-- prints a RAISE NOTICE, so the output should end with two notices. Then
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


