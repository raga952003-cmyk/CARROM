-- =============================================================================
-- Carrom Arena — pending migrations, combined
--
-- Everything the database is still missing, in one paste. Both parts are
-- idempotent: running this twice is harmless, and running it when half of it
-- is already applied is harmless too.
--
--   004  the toss: who called, who won it, and what they chose
--   005  the board detail behind a score, plus board locking and tie-breaks
--
-- Paste the whole file into the Supabase SQL editor and run it once. Then
-- GET /api/health should report  "migrations": "all applied".
-- =============================================================================

-- ==========================================================================
-- 004_match_toss.sql
-- ==========================================================================

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


-- ==========================================================================
-- 005_board_detail.sql
-- ==========================================================================

-- =============================================================================
-- 005 — Board detail: independent umpire observations
--
-- A board was stored as two numbers and a queen flag, so three separate facts
-- had to be squeezed into one: who won it, who took the queen, and whose coins
-- were left on the board. They are genuinely independent — a player can win the
-- board while their opponent covers the queen — and forcing them together made
-- real results impossible to record.
--
-- player1_score / player2_score keep their meaning as the FINAL board points,
-- so standings, print sheets and brackets are untouched. Everything added here
-- is the working that produced those numbers.
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

-- ---- what the umpire observed ---------------------------------------------

ALTER TABLE public.boards
    -- Who finished/won the board. Recorded, never inferred from the scores.
    ADD COLUMN IF NOT EXISTS board_winner TEXT
        CHECK (board_winner IS NULL OR board_winner IN ('player1', 'player2', 'none'));

ALTER TABLE public.boards
    ADD COLUMN IF NOT EXISTS p1_coins_pocketed INTEGER
        CHECK (p1_coins_pocketed IS NULL OR p1_coins_pocketed >= 0);

ALTER TABLE public.boards
    ADD COLUMN IF NOT EXISTS p2_coins_pocketed INTEGER
        CHECK (p2_coins_pocketed IS NULL OR p2_coins_pocketed >= 0);

ALTER TABLE public.boards
    -- Which side still had coins on the board when it ended.
    ADD COLUMN IF NOT EXISTS coins_remaining_with TEXT
        CHECK (coins_remaining_with IS NULL OR coins_remaining_with IN ('player1', 'player2', 'none'));

ALTER TABLE public.boards
    ADD COLUMN IF NOT EXISTS coins_remaining INTEGER
        CHECK (coins_remaining IS NULL OR coins_remaining >= 0);

-- ---- the queen, as two separate facts --------------------------------------

ALTER TABLE public.boards
    ADD COLUMN IF NOT EXISTS queen_pocketed_by TEXT
        CHECK (queen_pocketed_by IS NULL OR queen_pocketed_by IN ('player1', 'player2', 'none'));

ALTER TABLE public.boards
    -- May be the opponent of whoever pocketed it.
    ADD COLUMN IF NOT EXISTS queen_covered_by TEXT
        CHECK (queen_covered_by IS NULL OR queen_covered_by IN ('player1', 'player2', 'none'));

ALTER TABLE public.boards
    ADD COLUMN IF NOT EXISTS queen_status TEXT
        CHECK (queen_status IS NULL OR queen_status IN ('not_pocketed', 'covered', 'returned'));

ALTER TABLE public.boards
    ADD COLUMN IF NOT EXISTS queen_awarded_to TEXT
        CHECK (queen_awarded_to IS NULL OR queen_awarded_to IN ('player1', 'player2', 'none'));

-- ---- penalties --------------------------------------------------------------

ALTER TABLE public.boards
    ADD COLUMN IF NOT EXISTS p1_penalty INTEGER DEFAULT 0
        CHECK (p1_penalty IS NULL OR p1_penalty >= 0);

ALTER TABLE public.boards
    ADD COLUMN IF NOT EXISTS p2_penalty INTEGER DEFAULT 0
        CHECK (p2_penalty IS NULL OR p2_penalty >= 0);

-- ---- the working, kept so a result can be audited without re-deriving it ----

ALTER TABLE public.boards
    ADD COLUMN IF NOT EXISTS base_points INTEGER;

ALTER TABLE public.boards
    ADD COLUMN IF NOT EXISTS queen_bonus INTEGER;

ALTER TABLE public.boards
    -- Contradictions the umpire chose to record anyway, e.g. the winner also
    -- being the side with coins left. Surfaced, never silently corrected.
    ADD COLUMN IF NOT EXISTS scoring_warnings JSONB;

-- ---- confirmation and locking (spec 19, 20) --------------------------------

ALTER TABLE public.boards
    ADD COLUMN IF NOT EXISTS locked BOOLEAN DEFAULT false NOT NULL;

ALTER TABLE public.boards
    ADD COLUMN IF NOT EXISTS confirmed_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL;

ALTER TABLE public.boards
    ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ;

-- ---- tie-break at match level (spec 22, 23) --------------------------------

ALTER TABLE public.matches
    ADD COLUMN IF NOT EXISTS tie_break_required BOOLEAN DEFAULT false NOT NULL;

ALTER TABLE public.matches
    ADD COLUMN IF NOT EXISTS tie_break_rule TEXT;

ALTER TABLE public.matches
    ADD COLUMN IF NOT EXISTS tie_break_result TEXT;

CREATE INDEX IF NOT EXISTS idx_boards_locked ON public.boards(match_id, locked);


-- =============================================================================
-- apply_board_result — rewritten so a board patch is applied whole
--
-- The 002 version enumerated the columns it would write. Every column added
-- above would have been accepted by the API, passed into the patch, and then
-- silently dropped on the way through this function — leaving the atomic path
-- storing LESS than the non-atomic fallback beside it.
--
-- The patch is now merged onto the existing row as JSON, so a new column is
-- written the moment it exists and this function never needs editing again.
-- =============================================================================

CREATE OR REPLACE FUNCTION public.apply_board_result(
    p_match_id UUID,
    p_board_number INTEGER,
    p_board_patch JSONB,
    p_match_patch JSONB,
    p_audit JSONB,
    p_next_board_number INTEGER DEFAULT NULL
)
RETURNS JSONB AS $$
DECLARE
    v_prev  public.boards%ROWTYPE;
    v_next  public.boards%ROWTYPE;
    v_board public.boards%ROWTYPE;
BEGIN
    IF NOT public.is_admin_or_service() THEN
        RAISE EXCEPTION 'insufficient_privilege: admin rights required to apply a board result';
    END IF;

    -- Lock the board so two scorers cannot interleave on the same board.
    SELECT * INTO v_prev
    FROM public.boards
    WHERE match_id = p_match_id AND board_number = p_board_number
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'board_not_found: match % board %', p_match_id, p_board_number;
    END IF;

    -- Keys absent from the patch keep the value already on the row; the id and
    -- the board's identity are pinned so a stray key cannot move the row.
    v_next := jsonb_populate_record(
        v_prev,
        p_board_patch - 'id' - 'match_id' - 'board_number'
    );

    UPDATE public.boards SET
        player1_score        = v_next.player1_score,
        player2_score        = v_next.player2_score,
        status               = v_next.status,
        queen_claimed_by     = v_next.queen_claimed_by,
        queen_covered        = v_next.queen_covered,
        fouls_player1        = v_next.fouls_player1,
        fouls_player2        = v_next.fouls_player2,
        white_coins_pocketed = v_next.white_coins_pocketed,
        black_coins_pocketed = v_next.black_coins_pocketed,
        duration_minutes     = v_next.duration_minutes,
        notes                = v_next.notes,
        completed_at         = v_next.completed_at,
        board_winner         = v_next.board_winner,
        p1_coins_pocketed    = v_next.p1_coins_pocketed,
        p2_coins_pocketed    = v_next.p2_coins_pocketed,
        coins_remaining_with = v_next.coins_remaining_with,
        coins_remaining      = v_next.coins_remaining,
        queen_pocketed_by    = v_next.queen_pocketed_by,
        queen_covered_by     = v_next.queen_covered_by,
        queen_status         = v_next.queen_status,
        queen_awarded_to     = v_next.queen_awarded_to,
        p1_penalty           = v_next.p1_penalty,
        p2_penalty           = v_next.p2_penalty,
        base_points          = v_next.base_points,
        queen_bonus          = v_next.queen_bonus,
        scoring_warnings     = v_next.scoring_warnings,
        locked               = v_next.locked,
        confirmed_by         = v_next.confirmed_by,
        confirmed_at         = v_next.confirmed_at
    WHERE match_id = p_match_id AND board_number = p_board_number
    RETURNING * INTO v_board;

    INSERT INTO public.score_audit_logs (
        match_id, admin_id, admin_name, board_number, previous_score, new_score, reason
    ) VALUES (
        p_match_id,
        NULLIF(p_audit ->> 'admin_id', '')::UUID,
        COALESCE(p_audit ->> 'admin_name', 'System'),
        p_board_number,
        jsonb_build_object('player1', v_prev.player1_score, 'player2', v_prev.player2_score),
        COALESCE(p_audit -> 'new_score',
                 jsonb_build_object('player1', v_board.player1_score, 'player2', v_board.player2_score)),
        COALESCE(p_audit ->> 'reason', 'Score update')
    );

    UPDATE public.matches SET
        player1_board_wins   = COALESCE((p_match_patch ->> 'player1_board_wins')::INTEGER, player1_board_wins),
        player2_board_wins   = COALESCE((p_match_patch ->> 'player2_board_wins')::INTEGER, player2_board_wins),
        player1_total_points = COALESCE((p_match_patch ->> 'player1_total_points')::INTEGER, player1_total_points),
        player2_total_points = COALESCE((p_match_patch ->> 'player2_total_points')::INTEGER, player2_total_points),
        status               = COALESCE(p_match_patch ->> 'status', status),
        winner_id            = NULLIF(p_match_patch ->> 'winner_id', '')::UUID,
        winner_name          = NULLIF(p_match_patch ->> 'winner_name', ''),
        match_completed_at   = NULLIF(p_match_patch ->> 'match_completed_at', '')::TIMESTAMPTZ,
        tie_break_required   = COALESCE((p_match_patch ->> 'tie_break_required')::BOOLEAN, tie_break_required),
        tie_break_rule       = COALESCE(p_match_patch ->> 'tie_break_rule', tie_break_rule)
    WHERE id = p_match_id;

    -- Activate the following board only while the match is still running.
    IF p_next_board_number IS NOT NULL
       AND COALESCE(p_match_patch ->> 'status', '') <> 'completed' THEN
        UPDATE public.boards
        SET status = 'in_progress'
        WHERE match_id = p_match_id
          AND board_number = p_next_board_number
          AND status = 'pending';
    END IF;

    RETURN to_jsonb(v_board);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DO $$
BEGIN
    RAISE NOTICE 'Migration 005 applied: board detail, queen split, penalties, locking, tie-break.';
END $$;


-- ==========================================================================
-- 006_sets_and_sides.sql
-- ==========================================================================

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


-- ==========================================================================
-- 007_apply_board_result_sets.sql
-- ==========================================================================

-- =============================================================================
-- 007 — apply_board_result must know which set a board is in
--
-- The transactional write locked a board with
--     WHERE match_id = ... AND board_number = ...
-- which was unambiguous only while board numbers were unique per match. Once
-- board numbers restart each set, board 1 of a three-set match matches three
-- rows and the function fails with "query returned more than one row" — so
-- every score in a set-based match was rejected.
--
-- The set is now part of the lookup, defaulting to 1 so a match that is not
-- played in sets behaves exactly as before.
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
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'boards'
                     AND column_name = 'set_number') THEN
        RAISE EXCEPTION
            'boards.set_number is missing. Apply 006_sets_and_sides.sql first.';
    END IF;
END $$;

-- The old six-argument version has to go, or adding a defaulted seventh
-- argument leaves two candidates and every call becomes ambiguous.
DROP FUNCTION IF EXISTS public.apply_board_result(UUID, INTEGER, JSONB, JSONB, JSONB, INTEGER);

CREATE OR REPLACE FUNCTION public.apply_board_result(
    p_match_id UUID,
    p_board_number INTEGER,
    p_board_patch JSONB,
    p_match_patch JSONB,
    p_audit JSONB,
    p_next_board_number INTEGER DEFAULT NULL,
    p_set_number INTEGER DEFAULT 1
)
RETURNS JSONB AS $$
DECLARE
    v_prev  public.boards%ROWTYPE;
    v_next  public.boards%ROWTYPE;
    v_board public.boards%ROWTYPE;
    v_set   INTEGER := COALESCE(p_set_number, 1);
BEGIN
    IF NOT public.is_admin_or_service() THEN
        RAISE EXCEPTION 'insufficient_privilege: admin rights required to apply a board result';
    END IF;

    -- Lock the board so two scorers cannot interleave on the same board.
    SELECT * INTO v_prev
    FROM public.boards
    WHERE match_id = p_match_id
      AND board_number = p_board_number
      AND COALESCE(set_number, 1) = v_set
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'board_not_found: match % set % board %',
            p_match_id, v_set, p_board_number;
    END IF;

    -- Keys absent from the patch keep the value already on the row; the id and
    -- the board's identity are pinned so a stray key cannot move the row.
    v_next := jsonb_populate_record(
        v_prev,
        p_board_patch - 'id' - 'match_id' - 'board_number' - 'set_number'
    );

    UPDATE public.boards SET
        player1_score        = v_next.player1_score,
        player2_score        = v_next.player2_score,
        status               = v_next.status,
        queen_claimed_by     = v_next.queen_claimed_by,
        queen_covered        = v_next.queen_covered,
        fouls_player1        = v_next.fouls_player1,
        fouls_player2        = v_next.fouls_player2,
        white_coins_pocketed = v_next.white_coins_pocketed,
        black_coins_pocketed = v_next.black_coins_pocketed,
        duration_minutes     = v_next.duration_minutes,
        notes                = v_next.notes,
        completed_at         = v_next.completed_at,
        board_winner         = v_next.board_winner,
        p1_coins_pocketed    = v_next.p1_coins_pocketed,
        p2_coins_pocketed    = v_next.p2_coins_pocketed,
        p1_coins_remaining   = v_next.p1_coins_remaining,
        p2_coins_remaining   = v_next.p2_coins_remaining,
        coins_remaining_with = v_next.coins_remaining_with,
        coins_remaining      = v_next.coins_remaining,
        queen_pocketed       = v_next.queen_pocketed,
        queen_pocketed_by    = v_next.queen_pocketed_by,
        queen_covered_by     = v_next.queen_covered_by,
        queen_status         = v_next.queen_status,
        queen_awarded_to     = v_next.queen_awarded_to,
        p1_penalty           = v_next.p1_penalty,
        p2_penalty           = v_next.p2_penalty,
        base_points          = v_next.base_points,
        queen_bonus          = v_next.queen_bonus,
        scoring_warnings     = v_next.scoring_warnings,
        locked               = v_next.locked,
        confirmed_by         = v_next.confirmed_by,
        confirmed_at         = v_next.confirmed_at
    WHERE match_id = p_match_id
      AND board_number = p_board_number
      AND COALESCE(set_number, 1) = v_set
    RETURNING * INTO v_board;

    INSERT INTO public.score_audit_logs (
        match_id, admin_id, admin_name, board_number, previous_score, new_score, reason
    ) VALUES (
        p_match_id,
        NULLIF(p_audit ->> 'admin_id', '')::UUID,
        COALESCE(p_audit ->> 'admin_name', 'System'),
        p_board_number,
        jsonb_build_object('player1', v_prev.player1_score, 'player2', v_prev.player2_score),
        COALESCE(p_audit -> 'new_score',
                 jsonb_build_object('player1', v_board.player1_score, 'player2', v_board.player2_score)),
        COALESCE(p_audit ->> 'reason', 'Score update')
    );

    UPDATE public.matches SET
        player1_board_wins   = COALESCE((p_match_patch ->> 'player1_board_wins')::INTEGER, player1_board_wins),
        player2_board_wins   = COALESCE((p_match_patch ->> 'player2_board_wins')::INTEGER, player2_board_wins),
        player1_total_points = COALESCE((p_match_patch ->> 'player1_total_points')::INTEGER, player1_total_points),
        player2_total_points = COALESCE((p_match_patch ->> 'player2_total_points')::INTEGER, player2_total_points),
        player1_sets_won     = COALESCE((p_match_patch ->> 'player1_sets_won')::INTEGER, player1_sets_won),
        player2_sets_won     = COALESCE((p_match_patch ->> 'player2_sets_won')::INTEGER, player2_sets_won),
        status               = COALESCE(p_match_patch ->> 'status', status),
        winner_id            = NULLIF(p_match_patch ->> 'winner_id', '')::UUID,
        winner_name          = NULLIF(p_match_patch ->> 'winner_name', ''),
        match_completed_at   = NULLIF(p_match_patch ->> 'match_completed_at', '')::TIMESTAMPTZ,
        tie_break_required   = COALESCE((p_match_patch ->> 'tie_break_required')::BOOLEAN, tie_break_required),
        tie_break_rule       = COALESCE(p_match_patch ->> 'tie_break_rule', tie_break_rule)
    WHERE id = p_match_id;

    -- Activate the following board of the same set, while the match runs on.
    IF p_next_board_number IS NOT NULL
       AND COALESCE(p_match_patch ->> 'status', '') <> 'completed' THEN
        UPDATE public.boards
        SET status = 'in_progress'
        WHERE match_id = p_match_id
          AND board_number = p_next_board_number
          AND COALESCE(set_number, 1) = v_set
          AND status = 'pending';
    END IF;

    RETURN to_jsonb(v_board);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DO $$
BEGIN
    RAISE NOTICE 'Migration 007 applied: apply_board_result is set-aware.';
END $$;


-- ==========================================================================
-- 008_drop_city_default.sql
-- ==========================================================================

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

-- -----------------------------------------------------------------------------
-- Confirm it landed.
-- -----------------------------------------------------------------------------
DO $verify$
DECLARE
    missing TEXT := '';
BEGIN
    IF to_regclass('public.matches') IS NULL THEN
        RAISE EXCEPTION 'Base schema missing — run db/schema.sql first.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema='public' AND table_name='matches' AND column_name='toss_choice')
        THEN missing := missing || ' matches.toss_choice'; END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema='public' AND table_name='boards' AND column_name='board_winner')
        THEN missing := missing || ' boards.board_winner'; END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema='public' AND table_name='boards' AND column_name='locked')
        THEN missing := missing || ' boards.locked'; END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema='public' AND table_name='matches' AND column_name='tie_break_required')
        THEN missing := missing || ' matches.tie_break_required'; END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema='public' AND table_name='boards' AND column_name='set_number')
        THEN missing := missing || ' boards.set_number'; END IF;
    IF to_regclass('public.match_sets') IS NULL
        THEN missing := missing || ' table match_sets'; END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema='public' AND table_name='matches' AND column_name='player1_color')
        THEN missing := missing || ' matches.player1_color'; END IF;

    IF missing <> '' THEN
        RAISE EXCEPTION 'Migration did not complete. Still missing:%', missing;
    END IF;
    RAISE NOTICE 'All pending migrations applied. /api/health should now report "all applied".';
END $verify$;


------------------------------------------------------------------------------
-- 009_stop_timer_on_finish.sql
------------------------------------------------------------------------------

-- =============================================================================
-- 009 — Stop the match clock when the match ends
--
-- A match keeps its elapsed time in two halves: timer_elapsed_seconds, which is
-- only added to on pause, and timer_started_at, the epoch milliseconds of the
-- current run. The UI stops counting when is_timer_running goes false.
--
-- Nothing set that flag false when a match finished. Start, pause and resume
-- each maintained the timer, but the paths that END a match -- the last board
-- completing it, and the umpire confirming the result -- wrote
-- status = 'completed' and nothing else. The clock went on running on a match
-- that had already been won, and the stored duration stayed at whatever it was
-- at the last pause instead of the time the match actually took.
--
-- Four code paths reach 'completed': two in the application, and two inside
-- SECURITY DEFINER functions whose bodies differ depending on which of
-- migrations 002, 005 and 007 have been applied. Rather than rewrite three
-- variants of two functions, a trigger closes the clock whenever a match
-- arrives at 'completed' by any route at all -- including a hand correction in
-- the SQL editor.
--
-- The application stops the clock too, so a database without this migration
-- still behaves. The two do not double-count: the trigger only acts when the
-- flag is still set, and the application clears it in the same write.
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

CREATE OR REPLACE FUNCTION public.stop_timer_on_match_complete()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.status = 'completed' AND NEW.is_timer_running THEN
        -- Bank the stretch this run has been going before clearing the flag,
        -- or the recorded duration loses the final part of the match.
        NEW.timer_elapsed_seconds :=
            CASE
                WHEN NEW.timer_started_at IS NOT NULL THEN
                    COALESCE(NEW.timer_elapsed_seconds, 0)
                    + GREATEST(
                        0,
                        (((EXTRACT(EPOCH FROM now()) * 1000)::BIGINT - NEW.timer_started_at) / 1000)::INTEGER
                      )
                ELSE COALESCE(NEW.timer_elapsed_seconds, 0)
            END;
        NEW.is_timer_running := false;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_stop_timer_on_match_complete ON public.matches;
CREATE TRIGGER trg_stop_timer_on_match_complete
    BEFORE UPDATE ON public.matches
    FOR EACH ROW
    EXECUTE FUNCTION public.stop_timer_on_match_complete();

-- Matches already finished with the clock left running. How long they really
-- took is not recoverable, so only the flag is corrected -- which is the part
-- that stops the display counting.
UPDATE public.matches
SET is_timer_running = false
WHERE status = 'completed' AND is_timer_running = true;

DO $$
BEGIN
    RAISE NOTICE 'Migration 009 applied: the match clock now stops when a match completes.';
END $$;
