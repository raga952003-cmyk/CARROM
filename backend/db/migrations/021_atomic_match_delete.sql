-- Apply after 020 before routing DELETE /matches/{id} through this RPC.
-- A match and its propagated knockout slot are changed in one transaction.

-- Reserve each non-cash UPI/bank reference globally, including after a
-- refund. Cash receipt numbers are deliberately outside this constraint.
-- Existing duplicate paid references must be reconciled before applying it.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_payments_external_reference
  ON public.payments ((NULLIF(regexp_replace(
    upper(COALESCE(notes->>'reference', '')), '[^A-Z0-9]', '', 'g'
  ), '')))
  WHERE method IN ('upi', 'bank_transfer', 'gpay_upi')
    AND status IN ('paid', 'refunded', 'refund_due');

CREATE OR REPLACE FUNCTION public.delete_match_safely(
  p_match_id uuid,
  p_force boolean,
  p_actor_id uuid
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
  v_tournament_id uuid;
  v_tournament public.tournaments%ROWTYPE;
  v_match public.matches%ROWTYPE;
  v_parent public.matches%ROWTYPE;
  v_board_count integer;
  v_played_boards integer;
  v_has_play boolean;
  v_cleared_slot jsonb := NULL;
BEGIN
  SELECT tournament_id INTO v_tournament_id FROM public.matches
  WHERE id = p_match_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Match % does not exist', p_match_id;
  END IF;
  -- The tournament lock serializes against atomic fixture redraw and
  -- scheduling RPCs; the match lock blocks new feeder FK references.
  SELECT * INTO v_tournament FROM public.tournaments
  WHERE id = v_tournament_id FOR UPDATE;
  IF NOT FOUND OR v_tournament.status IN ('completed', 'cancelled') THEN
    RAISE EXCEPTION 'A completed or cancelled tournament cannot lose a match';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.profiles p WHERE p.id = p_actor_id
      AND p.role = 'admin'
      AND (
        v_tournament.owner_id = p_actor_id OR EXISTS (
          SELECT 1 FROM public.tournament_access a
          WHERE a.tournament_id = v_tournament_id
            AND a.user_id = p_actor_id AND a.status = 'approved'
            AND a.access_role = 'manager'
        )
      )
  ) THEN
    RAISE EXCEPTION 'Only this tournament owner or an approved manager may delete a match';
  END IF;
  SELECT * INTO v_match FROM public.matches
  WHERE id = p_match_id AND tournament_id = v_tournament_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Match changed during deletion; reload the draw';
  END IF;
  PERFORM 1 FROM public.boards WHERE match_id = p_match_id FOR UPDATE;

  -- Even force cannot orphan the winners of matches feeding this round.
  IF EXISTS (SELECT 1 FROM public.matches WHERE next_match_id = p_match_id) THEN
    RAISE EXCEPTION 'Delete the feeder matches first or redraw the knockout stage';
  END IF;
  SELECT count(*), count(*) FILTER (
    WHERE status = 'completed' OR player1_score <> 0 OR player2_score <> 0
  ) INTO v_board_count, v_played_boards
  FROM public.boards WHERE match_id = p_match_id;
  v_has_play := v_match.result_confirmed
    OR v_match.status IN ('live', 'paused', 'completed')
    OR v_match.winner_id IS NOT NULL OR v_played_boards > 0;
  IF v_has_play AND NOT COALESCE(p_force, false) THEN
    RAISE EXCEPTION 'Match has play or a result; force is required to delete it';
  END IF;

  IF v_match.next_match_id IS NOT NULL THEN
    SELECT * INTO v_parent FROM public.matches
    WHERE id = v_match.next_match_id FOR UPDATE;
    IF FOUND THEN
      IF v_parent.tournament_id <> v_tournament_id THEN
        RAISE EXCEPTION 'A knockout link points outside this tournament';
      END IF;
      IF v_parent.status IN ('live', 'paused', 'completed')
         OR v_parent.result_confirmed
         OR EXISTS (SELECT 1 FROM public.boards b
            WHERE b.match_id = v_parent.id
              AND (b.status = 'completed' OR b.player1_score <> 0
                   OR b.player2_score <> 0)) THEN
        RAISE EXCEPTION 'The next-round match already has play; it cannot lose a feeder';
      END IF;
      IF v_match.winner_id IS NOT NULL
         AND v_match.next_match_slot = 'player1'
         AND v_parent.player1_id = v_match.winner_id THEN
        UPDATE public.matches SET player1_id = NULL,
          player1_name = 'Winner TBD' WHERE id = v_parent.id;
        v_cleared_slot := jsonb_build_object(
          'matchNumber', v_parent.match_number, 'slot', 'player1');
      ELSIF v_match.winner_id IS NOT NULL
         AND v_match.next_match_slot = 'player2'
         AND v_parent.player2_id = v_match.winner_id THEN
        UPDATE public.matches SET player2_id = NULL,
          player2_name = 'Winner TBD' WHERE id = v_parent.id;
        v_cleared_slot := jsonb_build_object(
          'matchNumber', v_parent.match_number, 'slot', 'player2');
      END IF;
    END IF;
  END IF;

  DELETE FROM public.matches WHERE id = p_match_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Match changed during deletion; reload the draw';
  END IF;
  INSERT INTO public.audit_logs (
    user_id, action, entity_type, entity_id,
    previous_state, new_state, request_context
  ) VALUES (
    p_actor_id, 'match.delete', 'match', p_match_id::text,
    to_jsonb(v_match), jsonb_build_object('deleted', true),
    jsonb_build_object('forced', COALESCE(p_force, false),
      'boardsDeleted', v_board_count, 'boardsWithPlay', v_played_boards,
      'clearedSlot', v_cleared_slot)
  );
  RETURN jsonb_build_object(
    'match', to_jsonb(v_match),
    'tournamentId', v_tournament_id,
    'boardsDeleted', v_board_count,
    'boardsWithPlay', v_played_boards,
    'discardedPlay', v_has_play,
    'clearedSlot', v_cleared_slot
  );
END;
$$;

REVOKE ALL ON FUNCTION public.delete_match_safely(
  uuid, boolean, uuid
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.delete_match_safely(
  uuid, boolean, uuid
) TO service_role;

-- The last board of an official game and the first board of the next game
-- must move together. Opening the next game before writing the score could
-- expose an unplayed game after the score transaction failed.
CREATE OR REPLACE FUNCTION public.apply_board_result_with_next_set(
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
  v_next public.boards%ROWTYPE;
  v_result jsonb;
BEGIN
  IF p_next_set_number IS NULL OR p_next_set_number <> p_set_number + 1
     OR p_next_board_number IS NOT NULL
     OR p_match_patch->>'status' = 'completed' THEN
    RAISE EXCEPTION 'Invalid game transition';
  END IF;
  SELECT * INTO v_next FROM public.boards
  WHERE match_id = p_match_id AND set_number = p_next_set_number
    AND board_number = 1 FOR UPDATE;
  IF NOT FOUND OR v_next.status NOT IN ('pending', 'in_progress') THEN
    RAISE EXCEPTION 'The next game has no available first board';
  END IF;
  v_result := public.apply_board_result(
    p_match_id, p_board_number, p_board_patch, p_match_patch, p_audit,
    NULL, p_set_number
  );
  UPDATE public.boards SET status = 'in_progress'
  WHERE id = v_next.id AND status = 'pending';
  RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION public.apply_board_result_with_next_set(
  uuid, integer, jsonb, jsonb, jsonb, integer, integer, integer
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.apply_board_result_with_next_set(
  uuid, integer, jsonb, jsonb, jsonb, integer, integer, integer
) TO service_role;
