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
