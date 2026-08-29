-- =============================================================================
-- 008 — Stop defaulting everyone's city to Pune
--
-- profiles.city carried DEFAULT 'Pune', so any player created without a city
-- was recorded as being from Pune. That is a guess presented as a fact: it
-- shows on the player directory and prints on the draw sheet, and nobody typed
-- it. A city nobody supplied should be blank.
--
-- Existing rows are left alone. A player genuinely from Pune and a player who
-- was merely defaulted there are indistinguishable now, so clearing them would
-- discard real answers along with the guesses.
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

ALTER TABLE public.profiles ALTER COLUMN city DROP DEFAULT;

DO $$
BEGIN
    RAISE NOTICE 'Migration 008 applied: profiles.city no longer defaults to Pune.';
END $$;
