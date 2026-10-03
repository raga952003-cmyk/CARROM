-- Persist official board finish details in the same transaction as the score,
-- and settle a tied 21/6 game atomically. Apply after 029.
-- Safe to re-run.

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'boards'
      AND column_name = 'finish_type'
  ) OR NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'boards'
      AND column_name = 'special_finish_extra_point'
  ) OR NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'matches'
      AND column_name = 'set_tie_breaks'
  ) THEN
    RAISE EXCEPTION 'Apply 029_official_score_finishes_and_set_ties.sql before 030';
  END IF;
  IF to_regprocedure(
       'public.apply_board_result(uuid,integer,jsonb,jsonb,jsonb,integer,integer)'
     ) IS NULL OR to_regprocedure(
       'public.apply_board_result_with_next_set(uuid,integer,jsonb,jsonb,jsonb,integer,integer,integer)'
     ) IS NULL THEN
    RAISE EXCEPTION 'Apply score migrations 007 and 021 before 030';
  END IF;
END $$;

-- Migration 007 populates a boards row from p_board_patch, but its explicit
-- UPDATE list predates 029 and does not write the new finish columns. This
-- wrapper calls that existing score/audit/match transaction, then writes the
-- finish details before the transaction can commit. Migration 021's next-game
-- handoff is included when p_next_set_number is present.
CREATE OR REPLACE FUNCTION public.apply_official_board_result(
  p_match_id uuid,
  p_board_number integer,
  p_board_patch jsonb,
  p_match_patch jsonb,
  p_audit jsonb,
  p_next_board_number integer,
  p_set_number integer,
  p_next_set_number integer
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
  v_board public.boards%ROWTYPE;
  v_match public.matches%ROWTYPE;
  v_finish text := p_board_patch->>'finish_type';
  v_extra boolean := COALESCE(
    (p_board_patch->>'special_finish_extra_point')::boolean, false);
BEGIN
  -- All official score writes and sudden-death rulings take the match lock
  -- first. A stale scorer must reload rather than overwrite a new ruling.
  SELECT * INTO v_match FROM public.matches
  WHERE id = p_match_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Match not found';
  END IF;
  IF COALESCE(v_match.set_tie_breaks, '{}'::jsonb)
     IS DISTINCT FROM COALESCE(
       p_match_patch->'_expected_set_tie_breaks', '{}'::jsonb) THEN
    RAISE EXCEPTION 'A game ruling changed; reload the match before scoring';
  END IF;
  IF p_match_patch ? 'set_tie_breaks' THEN
    IF p_match_patch->'set_tie_breaks' IS DISTINCT FROM
       (COALESCE(v_match.set_tie_breaks, '{}'::jsonb)
        - COALESCE(p_set_number, 1)::text) THEN
      RAISE EXCEPTION 'Only the corrected game ruling may be cleared';
    END IF;
    IF EXISTS (
      SELECT 1 FROM public.boards
      WHERE match_id = p_match_id
        AND set_number > COALESCE(p_set_number, 1)
        AND status = 'completed'
    ) THEN
      RAISE EXCEPTION 'Correct later games before clearing this ruling';
    END IF;
  END IF;
  IF v_finish NOT IN ('normal', 'own_last_coin_queen_left')
     OR v_finish IS NULL THEN
    RAISE EXCEPTION 'An official board needs a valid finish type';
  END IF;
  IF v_extra AND v_finish <> 'own_last_coin_queen_left' THEN
    RAISE EXCEPTION 'The extra point needs a Law 107 finish';
  END IF;

  IF p_next_set_number IS NULL THEN
    PERFORM public.apply_board_result(
      p_match_id, p_board_number, p_board_patch, p_match_patch, p_audit,
      p_next_board_number, p_set_number);
  ELSE
    PERFORM public.apply_board_result_with_next_set(
      p_match_id, p_board_number, p_board_patch, p_match_patch, p_audit,
      p_next_board_number, p_set_number, p_next_set_number);
  END IF;

  UPDATE public.boards
  SET finish_type = v_finish,
      special_finish_extra_point = v_extra
  WHERE match_id = p_match_id
    AND board_number = p_board_number
    AND COALESCE(set_number, 1) = COALESCE(p_set_number, 1)
  RETURNING * INTO v_board;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Board disappeared during official scoring';
  END IF;
  IF p_match_patch ? 'set_tie_breaks' THEN
    UPDATE public.matches
    SET set_tie_breaks = p_match_patch->'set_tie_breaks'
    WHERE id = p_match_id;
    INSERT INTO public.audit_logs (
      user_id, action, entity_type, entity_id,
      previous_state, new_state, request_context
    ) VALUES (
      NULLIF(p_audit->>'admin_id', '')::uuid,
      'match.set_tie_break_invalidated', 'match', p_match_id::text,
      jsonb_build_object('setTieBreaks', v_match.set_tie_breaks),
      jsonb_build_object('setTieBreaks', p_match_patch->'set_tie_breaks'),
      jsonb_build_object('setNumber', COALESCE(p_set_number, 1),
                         'reason', p_audit->>'reason')
    );
  END IF;
  IF COALESCE((p_match_patch->>'_hold_next_set')::boolean, false) THEN
    UPDATE public.boards SET status = 'pending'
    WHERE match_id = p_match_id
      AND set_number = COALESCE(p_set_number, 1) + 1
      AND board_number = 1 AND status = 'in_progress';
  END IF;
  IF COALESCE((p_match_patch->>'_open_next_set')::boolean, false) THEN
    UPDATE public.boards SET status = 'in_progress'
    WHERE match_id = p_match_id
      AND set_number = COALESCE(p_set_number, 1) + 1
      AND board_number = 1 AND status = 'pending';
  END IF;
  RETURN to_jsonb(v_board);
END;
$$;

REVOKE ALL ON FUNCTION public.apply_official_board_result(
  uuid, integer, jsonb, jsonb, jsonb, integer, integer, integer
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.apply_official_board_result(
  uuid, integer, jsonb, jsonb, jsonb, integer, integer, integer
) TO service_role;

-- Two umpires may submit a sudden-death ruling at the same time. Lock the
-- match, recheck the six finished boards, and write the ruling, match result,
-- next-game activation, and audit in one database transaction.
CREATE OR REPLACE FUNCTION public.record_set_tie_break(
  p_match_id uuid,
  p_set_number integer,
  p_winner_id uuid,
  p_reason text,
  p_match_patch jsonb,
  p_actor_id uuid
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
  v_match public.matches%ROWTYPE;
  v_updated public.matches%ROWTYPE;
  v_rules jsonb;
  v_tournament_status text;
  v_existing jsonb;
  v_decision jsonb;
  v_boards integer;
  v_completed integer;
  v_p1_points integer;
  v_p2_points integer;
  v_p1_sets integer;
  v_p2_sets integer;
  v_total_sets integer;
  v_required_sets integer;
  v_match_complete boolean;
  v_winner_name text;
  v_next_board_id uuid;
BEGIN
  IF p_set_number IS NULL OR p_set_number < 1
     OR p_actor_id IS NULL OR p_reason IS NULL
     OR length(btrim(p_reason)) < 5 THEN
    RAISE EXCEPTION 'A game number, acting umpire and reason are required';
  END IF;

  SELECT * INTO v_match FROM public.matches
  WHERE id = p_match_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Match not found';
  END IF;
  IF v_match.result_confirmed OR v_match.status NOT IN ('live', 'paused') THEN
    RAISE EXCEPTION 'This match cannot accept a game ruling';
  END IF;
  IF p_winner_id IS NULL OR (
      p_winner_id IS DISTINCT FROM v_match.player1_id
      AND p_winner_id IS DISTINCT FROM v_match.player2_id
  ) THEN
    RAISE EXCEPTION 'The ruling winner is not a match participant';
  END IF;

  SELECT rules, status INTO v_rules, v_tournament_status
  FROM public.tournaments WHERE id = v_match.tournament_id;
  IF NOT FOUND OR v_tournament_status IN ('completed', 'cancelled') THEN
    RAISE EXCEPTION 'This tournament cannot accept a game ruling';
  END IF;
  IF COALESCE(v_rules->>'scoringMode', '') <> 'official_icf'
     OR COALESCE(v_rules->>'setWinnerRule', '') <> 'target_points'
     OR COALESCE((v_rules->>'targetScore')::integer, 25) <> 21
     OR COALESCE((v_rules->>'boardsPerSet')::integer, 8) <> 6 THEN
    RAISE EXCEPTION 'Sudden death here requires the official 21/6 rules';
  END IF;

  v_total_sets := COALESCE(v_match.number_of_sets, 3);
  IF p_set_number > v_total_sets THEN
    RAISE EXCEPTION 'Game number is outside this match';
  END IF;
  v_existing := COALESCE(v_match.set_tie_breaks, '{}'::jsonb)
    -> p_set_number::text;
  IF v_existing IS NOT NULL THEN
    IF v_existing->>'winnerId' = p_winner_id::text
       AND v_existing->>'method' = 'sudden_death' THEN
      RETURN to_jsonb(v_match);
    END IF;
    RAISE EXCEPTION 'This game already has a different ruling';
  END IF;

  SELECT count(*), count(*) FILTER (WHERE status = 'completed'),
         COALESCE(sum(player1_score), 0), COALESCE(sum(player2_score), 0)
  INTO v_boards, v_completed, v_p1_points, v_p2_points
  FROM public.boards
  WHERE match_id = p_match_id AND COALESCE(set_number, 1) = p_set_number;
  IF v_boards <> 6 OR v_completed <> 6 OR v_p1_points <> v_p2_points THEN
    RAISE EXCEPTION 'Sudden death requires exactly six completed tied boards';
  END IF;
  -- Reproduce the engine's stop-at-21 rule under the same match lock. Six
  -- final scores can be level even though a side had already won on board 4.
  IF EXISTS (
    SELECT 1 FROM (
      SELECT board_number,
             sum(player1_score) OVER (ORDER BY board_number) AS p1_running,
             sum(player2_score) OVER (ORDER BY board_number) AS p2_running
      FROM public.boards
      WHERE match_id = p_match_id
        AND COALESCE(set_number, 1) = p_set_number
    ) AS running
    WHERE board_number < 6
      AND greatest(p1_running, p2_running) >= 21
      AND p1_running <> p2_running
  ) THEN
    RAISE EXCEPTION 'The game was already won before its sixth board';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.boards
    WHERE match_id = p_match_id AND set_number > p_set_number
      AND status = 'completed'
  ) THEN
    RAISE EXCEPTION 'A later game has already been scored';
  END IF;

  v_p1_sets := v_match.player1_sets_won
    + CASE WHEN p_winner_id = v_match.player1_id THEN 1 ELSE 0 END;
  v_p2_sets := v_match.player2_sets_won
    + CASE WHEN p_winner_id = v_match.player2_id THEN 1 ELSE 0 END;
  IF COALESCE((p_match_patch->>'player1_sets_won')::integer, -1) <> v_p1_sets
     OR COALESCE((p_match_patch->>'player2_sets_won')::integer, -1) <> v_p2_sets THEN
    RAISE EXCEPTION 'The match changed before the ruling was saved; reload it';
  END IF;
  v_required_sets := v_total_sets / 2 + 1;
  v_match_complete := v_p1_sets >= v_required_sets
    OR v_p2_sets >= v_required_sets;
  v_winner_name := CASE WHEN p_winner_id = v_match.player1_id
    THEN v_match.player1_name ELSE v_match.player2_name END;
  v_decision := jsonb_build_object(
    'method', 'sudden_death', 'winnerId', p_winner_id::text,
    'winnerName', v_winner_name, 'reason', btrim(p_reason),
    'decidedBy', p_actor_id::text, 'decidedAt', now());

  UPDATE public.matches
  SET set_tie_breaks = jsonb_set(
        COALESCE(v_match.set_tie_breaks, '{}'::jsonb),
        ARRAY[p_set_number::text], v_decision, true),
      player1_sets_won = v_p1_sets,
      player2_sets_won = v_p2_sets,
      tie_break_required = false,
      tie_break_rule = 'sudden_death',
      status = CASE WHEN v_match_complete THEN 'completed' ELSE status END,
      winner_id = CASE WHEN v_match_complete THEN p_winner_id ELSE NULL END,
      winner_name = CASE WHEN v_match_complete THEN v_winner_name ELSE NULL END,
      match_completed_at = CASE WHEN v_match_complete THEN now()
                                ELSE match_completed_at END
  WHERE id = p_match_id
  RETURNING * INTO v_updated;

  IF NOT v_match_complete THEN
    UPDATE public.boards SET status = 'in_progress'
    WHERE match_id = p_match_id AND set_number = p_set_number + 1
      AND board_number = 1 AND status IN ('pending', 'in_progress')
    RETURNING id INTO v_next_board_id;
    IF v_next_board_id IS NULL THEN
      RAISE EXCEPTION 'The next game has no available first board';
    END IF;
  END IF;

  INSERT INTO public.audit_logs (
    user_id, action, entity_type, entity_id,
    previous_state, new_state, request_context
  ) VALUES (
    p_actor_id, 'match.set_tie_break', 'match', p_match_id::text,
    to_jsonb(v_match),
    jsonb_build_object('setNumber', p_set_number, 'decision', v_decision),
    jsonb_build_object('atomic', true));
  RETURN to_jsonb(v_updated);
END;
$$;

REVOKE ALL ON FUNCTION public.record_set_tie_break(
  uuid, integer, uuid, text, jsonb, uuid
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.record_set_tie_break(
  uuid, integer, uuid, text, jsonb, uuid
) TO service_role;

CREATE OR REPLACE FUNCTION public.official_score_atomic_ready()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
  SELECT pg_catalog.to_regprocedure(
    'public.apply_official_board_result(uuid,integer,jsonb,jsonb,jsonb,integer,integer,integer)'
  ) IS NOT NULL
  AND pg_catalog.to_regprocedure(
    'public.record_set_tie_break(uuid,integer,uuid,text,jsonb,uuid)'
  ) IS NOT NULL
  AND pg_catalog.to_regprocedure(
    'public.apply_board_result(uuid,integer,jsonb,jsonb,jsonb,integer,integer)'
  ) IS NOT NULL
  AND pg_catalog.to_regprocedure(
    'public.apply_board_result_with_next_set(uuid,integer,jsonb,jsonb,jsonb,integer,integer,integer)'
  ) IS NOT NULL;
$$;

REVOKE ALL ON FUNCTION public.official_score_atomic_ready()
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.official_score_atomic_ready()
  TO service_role;

DO $$ BEGIN
  RAISE NOTICE 'Migration 030 applied: official board finishes and six-board sudden death are atomic.';
END $$;
