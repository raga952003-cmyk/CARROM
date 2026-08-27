-- =============================================================================
-- 004 — Match toss
--
-- A carrom match starts with a toss: a coin decides which side calls, the
-- winning side is recorded, and they choose either to strike first or to take
-- a side. None of that was stored, so the umpire's decision lived only in
-- their head and could not be shown on the match card, printed, or audited.
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
    ADD COLUMN IF NOT EXISTS toss_coin_result TEXT
        CHECK (toss_coin_result IS NULL OR toss_coin_result IN ('black', 'white'));

ALTER TABLE public.matches
    -- The side that won the toss: a profile id for singles, a team id for
    -- doubles, matching player1_id / player2_id on the same row.
    ADD COLUMN IF NOT EXISTS toss_winner_id UUID;

ALTER TABLE public.matches
    ADD COLUMN IF NOT EXISTS toss_winner_name TEXT;

ALTER TABLE public.matches
    -- 'strike' = take the striker and break first; 'side' = choose the side.
    ADD COLUMN IF NOT EXISTS toss_choice TEXT
        CHECK (toss_choice IS NULL OR toss_choice IN ('strike', 'side'));

ALTER TABLE public.matches
    ADD COLUMN IF NOT EXISTS toss_recorded_at TIMESTAMPTZ;

ALTER TABLE public.matches
    ADD COLUMN IF NOT EXISTS toss_recorded_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_matches_toss ON public.matches(tournament_id, toss_recorded_at);

DO $$
BEGIN
    RAISE NOTICE 'Migration 004 applied: match toss recorded on matches.';
END $$;
