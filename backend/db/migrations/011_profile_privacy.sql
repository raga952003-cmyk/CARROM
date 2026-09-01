-- =============================================================================
-- 011 — Stop publishing everyone's email address and phone number
--
-- The policy was:
--     CREATE POLICY select_profiles ON public.profiles
--         FOR SELECT TO public USING (true);
--
-- Every row, every column, to anyone. And "anyone" is literal: the anon key
-- that authorises those reads is embedded in the frontend JavaScript bundle
-- served to every visitor, so extracting it and dumping the table takes a
-- browser and about a minute. Verified against the live database with nothing
-- but that public key: 22 profiles came back, with names, emails, clubs,
-- cities and a phone number.
--
-- What the app genuinely needs to show publicly is who is playing: a name, a
-- club, a rating, an avatar. It never needs to show a stranger's email address
-- or phone number.
--
-- So the table keeps its public read, and the contact columns move to a view
-- that the app reads instead. A player still sees their own details, and an
-- admin still sees everyone's -- both of those already had policies and are
-- untouched.
--
-- NOTE: writes were already safe. update_profiles_self requires
-- auth.uid() = id, so the anon key could read but never modify. This migration
-- closes the read.
--
-- Safe to re-run.
-- =============================================================================

DO $$
BEGIN
    IF to_regclass('public.profiles') IS NULL THEN
        RAISE EXCEPTION
            'Base schema missing in this database (current_database=%). '
            'Run db/schema.sql first, or switch to the project whose ref '
            'matches SUPABASE_URL in backend/.env.', current_database();
    END IF;
END $$;

-- Replace the blanket public read with one that only covers a person's own row
-- (admins are already covered by admin_all_profiles, which is FOR ALL).
DROP POLICY IF EXISTS select_profiles ON public.profiles;

CREATE POLICY select_own_profile ON public.profiles
    FOR SELECT USING (auth.uid() = id);

-- The public directory: who is playing, without how to contact them.
CREATE OR REPLACE VIEW public.public_profiles
WITH (security_invoker = false) AS
    SELECT id, name, avatar, club, city, rating, role, created_at
    FROM public.profiles;

GRANT SELECT ON public.public_profiles TO anon, authenticated;

COMMENT ON VIEW public.public_profiles IS
    'Player directory without contact details. Read this instead of profiles '
    'from any browser-side query; profiles itself is now restricted to the '
    'owner of the row and to admins.';

DO $$
BEGIN
    RAISE NOTICE 'Migration 011 applied: emails and phone numbers are no longer world-readable.';
END $$;
