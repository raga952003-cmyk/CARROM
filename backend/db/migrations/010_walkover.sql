-- =============================================================================
-- 010 — Record a walkover
--
-- A player does not turn up, retires injured, or concedes. Until now there was
-- no way to say so: 'match.walkover' existed as a permission but no endpoint
-- implemented it, MatchUpdateSchema was imported and never used, and there was
-- no PUT /matches/{id} at all. The only way to finish a match was to score
-- boards, so an organiser facing a no-show had to invent board scores — which
-- then flowed into the points table as though they had been played.
--
-- These two columns let the result say what actually happened. The match still
-- carries a winner, board wins and points so the standings work unchanged; the
-- flag records that no carrom was played, and the reason says why.
--
-- Safe to re-run.
-- =============================================================================

DO $$
BEGIN
    IF to_regclass('public.matches') IS NULL THEN
        RAISE EXCEPTION
            'Base schema missing in this database (current_database=%). '
            'Run db/schema.sql first, or switch to the project whose ref '
            'matches SUPABASE_URL in backend/.env.', current_database();
    END IF;
END $$;

ALTER TABLE public.matches
    ADD COLUMN IF NOT EXISTS walkover BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE public.matches
    ADD COLUMN IF NOT EXISTS walkover_reason TEXT;

-- Who recorded it, so a result nobody played is still accountable.
ALTER TABLE public.matches
    ADD COLUMN IF NOT EXISTS walkover_by UUID;

DO $$
BEGIN
    RAISE NOTICE 'Migration 010 applied: matches can be recorded as walkovers.';
END $$;
