-- =============================================================================
-- 012 — Tournament lifecycle: a champion, and a way to call it off
--
-- A tournament could be created, opened, closed and drawn, and then nothing:
-- no endpoint ever moved it to in_progress or completed, nothing recorded who
-- won, and there was no way to cancel one at all. The status column's CHECK
-- did not even accept 'cancelled' on databases that never had migration 002
-- applied, so the API had no legal value to write.
--
-- This adds the facts a finished tournament needs to carry -- the champion,
-- when it ended -- and the facts a cancelled one needs -- when, and why. The
-- API probes tournaments.champion_id to decide whether it may write any of
-- them; without this migration it still moves the status and simply leaves
-- these unrecorded.
--
-- The CHECK is re-asserted here rather than assumed from 002, because a
-- database is allowed to be missing 002 and still run: set_tournament_status
-- writes the legacy synonyms 'scheduled' and 'ongoing' when the new names are
-- refused. 'cancelled' and 'completed' have no synonym, so for those the CHECK
-- has to be right. Every name 002 allows is kept, the legacy pair included, so
-- no existing row becomes invalid.
--
-- Safe to re-run.
-- =============================================================================

DO $$
BEGIN
    IF to_regclass('public.tournaments') IS NULL OR to_regclass('public.profiles') IS NULL THEN
        RAISE EXCEPTION
            'Base schema missing in this database (current_database=%). '
            'Run db/schema.sql first, or switch to the project whose ref '
            'matches SUPABASE_URL in backend/.env.', current_database();
    END IF;
END $$;

-- -----------------------------------------------------------------------------
-- 1. The full status vocabulary, cancellation included
-- -----------------------------------------------------------------------------
ALTER TABLE public.tournaments DROP CONSTRAINT IF EXISTS tournaments_status_check;
ALTER TABLE public.tournaments ADD CONSTRAINT tournaments_status_check
  CHECK (status IN (
    'draft',
    'registration_open',
    'registration_closed',
    'fixture_generation',
    'fixture_published',
    'in_progress',
    'completed',
    'cancelled',
    'scheduled',
    'ongoing'
  ));

-- -----------------------------------------------------------------------------
-- 2. What a completed tournament records
--
-- champion_id points at a profile, so it is filled for a singles champion and
-- left NULL for a doubles pair -- a team id is not a profile id. champion_name
-- is what the poster and the notification actually show, and it survives the
-- profile being deleted.
-- -----------------------------------------------------------------------------
ALTER TABLE public.tournaments
    ADD COLUMN IF NOT EXISTS champion_id UUID REFERENCES public.profiles(id) ON DELETE SET NULL;

ALTER TABLE public.tournaments
    ADD COLUMN IF NOT EXISTS champion_name TEXT;

ALTER TABLE public.tournaments
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;

-- -----------------------------------------------------------------------------
-- 3. What a cancelled tournament records
-- -----------------------------------------------------------------------------
ALTER TABLE public.tournaments
    ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ;

ALTER TABLE public.tournaments
    ADD COLUMN IF NOT EXISTS cancel_reason TEXT;

DO $$
BEGIN
    RAISE NOTICE 'Migration 012 applied: tournaments can be started, completed with a champion, and cancelled with a reason.';
END $$;
