-- =============================================================================
-- 006 — Sets, sides and table assignment (Carromite format)
--
-- A match was a flat list of boards. The Carromite format puts a SET between
-- them: 3 sets of 8 boards is 24 boards, the set is won on total points within
-- it, and the match is won on sets — so a player can score fewer points overall
-- and still win, which a flat board list cannot express at all.
--
-- Everything here is additive. Existing matches become a single set of the
-- boards they already have, and score exactly as they did before.
--
-- Safe to re-run.
-- =============================================================================

DO $$
BEGIN
    IF to_regclass('public.boards') IS NULL THEN
        RAISE EXCEPTION
            'Base schema missing in this database (current_database=%). '
            'Run db/schema.sql first, or switch to the project whose ref '
            'matches SUPABASE_URL in backend/.env.', current_database();
    END IF;
END $$;

-- ---- tournament configuration ----------------------------------------------

ALTER TABLE public.tournaments
    ADD COLUMN IF NOT EXISTS number_of_sets INTEGER DEFAULT 1
        CHECK (number_of_sets IS NULL OR number_of_sets > 0);

ALTER TABLE public.tournaments
    -- Boards within one set. Falls back to matches.max_boards when unset, so a
    -- tournament configured before sets existed keeps its shape.
    ADD COLUMN IF NOT EXISTS boards_per_set INTEGER
        CHECK (boards_per_set IS NULL OR boards_per_set > 0);

-- ---- which set a board belongs to ------------------------------------------

ALTER TABLE public.boards
    ADD COLUMN IF NOT EXISTS set_number INTEGER NOT NULL DEFAULT 1
        CHECK (set_number > 0);

-- Board numbers restart each set, so uniqueness is per set, not per match.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'unique_board_per_match'
          AND conrelid = 'public.boards'::regclass
    ) THEN
        ALTER TABLE public.boards DROP CONSTRAINT unique_board_per_match;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'unique_board_per_set'
          AND conrelid = 'public.boards'::regclass
    ) THEN
        ALTER TABLE public.boards
            ADD CONSTRAINT unique_board_per_set UNIQUE (match_id, set_number, board_number);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_boards_set ON public.boards(match_id, set_number, board_number);

-- ---- the set itself ---------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.match_sets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    match_id UUID NOT NULL REFERENCES public.matches(id) ON DELETE CASCADE,
    set_number INTEGER NOT NULL CHECK (set_number > 0),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'in_progress', 'completed')),
    -- Points accumulated across the boards of this set.
    player1_points INTEGER DEFAULT 0 NOT NULL,
    player2_points INTEGER DEFAULT 0 NOT NULL,
    winner_id UUID,
    winner_name TEXT,
    completed_at TIMESTAMPTZ,
    CONSTRAINT unique_set_per_match UNIQUE (match_id, set_number)
);

CREATE INDEX IF NOT EXISTS idx_match_sets_match ON public.match_sets(match_id, set_number);

ALTER TABLE public.match_sets ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE tablename = 'match_sets' AND policyname = 'select_match_sets') THEN
        CREATE POLICY select_match_sets ON public.match_sets FOR SELECT USING (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE tablename = 'match_sets' AND policyname = 'write_match_sets') THEN
        -- Writes go through the API, which does its own authorisation.
        CREATE POLICY write_match_sets ON public.match_sets FOR ALL
            USING (public.is_admin_or_service()) WITH CHECK (public.is_admin_or_service());
    END IF;
EXCEPTION WHEN undefined_function THEN
    -- is_admin_or_service() ships with migration 002; without it, leave the
    -- table readable and let the service key handle writes.
    NULL;
END $$;

-- ---- match level: sets won, sides, table, referee ---------------------------

ALTER TABLE public.matches
    ADD COLUMN IF NOT EXISTS number_of_sets INTEGER DEFAULT 1
        CHECK (number_of_sets IS NULL OR number_of_sets > 0);

ALTER TABLE public.matches
    ADD COLUMN IF NOT EXISTS player1_sets_won INTEGER DEFAULT 0 NOT NULL;

ALTER TABLE public.matches
    ADD COLUMN IF NOT EXISTS player2_sets_won INTEGER DEFAULT 0 NOT NULL;

ALTER TABLE public.matches
    -- The coin each side plays. Stored against the player id, never against a
    -- screen position, so switching sides on screen cannot reassign it.
    ADD COLUMN IF NOT EXISTS player1_color TEXT
        CHECK (player1_color IS NULL OR player1_color IN ('black', 'white'));

ALTER TABLE public.matches
    ADD COLUMN IF NOT EXISTS player2_color TEXT
        CHECK (player2_color IS NULL OR player2_color IN ('black', 'white'));

ALTER TABLE public.matches
    -- Which side is drawn on the left. Presentation only: player1_id stays
    -- player1_id whichever way round the umpire is standing.
    ADD COLUMN IF NOT EXISTS sides_swapped BOOLEAN DEFAULT false NOT NULL;

ALTER TABLE public.matches
    ADD COLUMN IF NOT EXISTS table_number INTEGER;

ALTER TABLE public.matches
    ADD COLUMN IF NOT EXISTS referee_id UUID REFERENCES public.profiles(id) ON DELETE SET NULL;

ALTER TABLE public.matches
    ADD COLUMN IF NOT EXISTS referee_name TEXT;

-- Both sides cannot play the same colour.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'distinct_coin_colors' AND conrelid = 'public.matches'::regclass
    ) THEN
        ALTER TABLE public.matches
            ADD CONSTRAINT distinct_coin_colors CHECK (
                player1_color IS NULL OR player2_color IS NULL
                OR player1_color <> player2_color
            );
    END IF;
END $$;

-- ---- board detail the Carromite sheet records -------------------------------

ALTER TABLE public.boards
    -- Both sides' remaining counts, not just the one side that has coins left.
    ADD COLUMN IF NOT EXISTS p1_coins_remaining INTEGER
        CHECK (p1_coins_remaining IS NULL OR p1_coins_remaining >= 0);

ALTER TABLE public.boards
    ADD COLUMN IF NOT EXISTS p2_coins_remaining INTEGER
        CHECK (p2_coins_remaining IS NULL OR p2_coins_remaining >= 0);

ALTER TABLE public.boards
    -- Recorded explicitly rather than inferred from queen_pocketed_by, so
    -- "not pocketed" and "pocketed by nobody yet" stay distinguishable.
    ADD COLUMN IF NOT EXISTS queen_pocketed BOOLEAN;

DO $$
BEGIN
    RAISE NOTICE 'Migration 006 applied: sets, coin colours, side swap, table and referee.';
END $$;
