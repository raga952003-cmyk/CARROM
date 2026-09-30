-- =============================================================================
-- 029 — Official carrom: special board finishes, and sudden death per game
--
-- The official ICF/AICF preset needs three facts the schema had nowhere to
-- put. schema.sql gained them for a fresh install; this is the same change
-- for a database that already exists. Without it the scoring code writes to
-- columns that are not there and every board submission in an official-preset
-- match fails — while /api/health reports "all applied", because nothing was
-- probing for them.
--
--   boards.finish_type
--       How the board ended. 'normal', or 'own_last_coin_queen_left' —
--       Law 107: a player who pockets their own last coin while the queen is
--       still on the board concedes the board, and the OPPONENT scores.
--
--   boards.special_finish_extra_point
--       The additional point for the improper stroke that goes with it. Only
--       meaningful alongside that finish, so a CHECK ties the two together
--       rather than trusting every writer to remember.
--
--   matches.set_tie_breaks
--       The umpire's sudden-death ruling on ONE game, keyed by game number as
--       a string: {"1": {"method": "sudden_death", "winnerId": ...}}. The
--       21-point/six-board age-group variant settles a level sixth board this
--       way instead of playing a seventh — which matters because a seventh
--       board would manufacture coins and move net score difference, and that
--       is the league's tie-break. summarise_sets reads exactly this shape.
--
-- Every column takes a DEFAULT that reproduces today's behaviour, so existing
-- rows are correct the moment they exist: every board already played was a
-- 'normal' finish with no extra point, and no game has been ruled on.
--
-- Safe to re-run.
-- =============================================================================

DO $$
BEGIN
    IF to_regclass('public.boards') IS NULL OR to_regclass('public.matches') IS NULL THEN
        RAISE EXCEPTION 'public.boards and public.matches must exist before 029.';
    END IF;
END $$;

-- ---- boards: how the board finished ----------------------------------------
ALTER TABLE public.boards
    ADD COLUMN IF NOT EXISTS finish_type TEXT NOT NULL DEFAULT 'normal';

ALTER TABLE public.boards
    ADD COLUMN IF NOT EXISTS special_finish_extra_point BOOLEAN NOT NULL DEFAULT false;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.boards'::regclass
          AND conname = 'boards_finish_type_check'
    ) THEN
        ALTER TABLE public.boards
            ADD CONSTRAINT boards_finish_type_check
            CHECK (finish_type IN ('normal', 'own_last_coin_queen_left'));
    END IF;

    -- The extra point is for the improper stroke that causes the Law 107
    -- finish. It cannot stand on its own: a 'normal' board carrying one would
    -- award a point nothing happened for.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.boards'::regclass
          AND conname = 'special_finish_extra_point_requires_finish'
    ) THEN
        ALTER TABLE public.boards
            ADD CONSTRAINT special_finish_extra_point_requires_finish
            CHECK (
                NOT special_finish_extra_point
                OR finish_type = 'own_last_coin_queen_left'
            );
    END IF;
END $$;

-- ---- matches: the per-game sudden-death ruling ------------------------------
ALTER TABLE public.matches
    ADD COLUMN IF NOT EXISTS set_tie_breaks JSONB NOT NULL DEFAULT '{}'::jsonb;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.matches'::regclass
          AND conname = 'matches_set_tie_breaks_is_object'
    ) THEN
        ALTER TABLE public.matches
            ADD CONSTRAINT matches_set_tie_breaks_is_object
            CHECK (jsonb_typeof(set_tie_breaks) = 'object');
    END IF;
END $$;

DO $$
BEGIN
    RAISE NOTICE 'Migration 029 applied: boards.finish_type and boards.special_finish_extra_point added (with their CHECKs), matches.set_tie_breaks added. Official-preset scoring and per-game sudden death can now be recorded.';
END $$;
