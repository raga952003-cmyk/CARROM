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
