-- Apply after 018, before routing fixture generation or scheduling through
-- these RPCs. A PostgREST RPC call runs in one database transaction: a bad
-- board, bracket link, or schedule row rolls back the entire operation.

CREATE OR REPLACE FUNCTION public.replace_tournament_fixtures(
  p_tournament_id uuid,
  p_matches jsonb,
  p_boards jsonb,
  p_links jsonb,
  p_force boolean DEFAULT false
)
RETURNS integer
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
  v_tournament public.tournaments%ROWTYPE;
  v_match_count integer;
  v_link_count integer;
  v_updated integer;
BEGIN
  -- This row lock serializes redraws across serverless instances. Match and
  -- board locks also keep a score write from overtaking the played guard.
  SELECT * INTO v_tournament FROM public.tournaments
  WHERE id = p_tournament_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Tournament % does not exist', p_tournament_id;
  END IF;
  IF v_tournament.status IN ('completed', 'cancelled') THEN
    RAISE EXCEPTION 'A completed or cancelled tournament cannot be redrawn';
  END IF;
  IF jsonb_typeof(p_matches) IS DISTINCT FROM 'array'
     OR jsonb_typeof(p_boards) IS DISTINCT FROM 'array'
     OR jsonb_typeof(p_links) IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION 'matches, boards and links must be JSON arrays';
  END IF;
  v_match_count := jsonb_array_length(p_matches);
  v_link_count := jsonb_array_length(p_links);
  IF v_match_count = 0 OR jsonb_array_length(p_boards) = 0 THEN
    RAISE EXCEPTION 'A draw needs matches and boards';
  END IF;
  IF (SELECT count(DISTINCT (item->>'id')::uuid)
      FROM jsonb_array_elements(p_matches) item) <> v_match_count THEN
    RAISE EXCEPTION 'Match IDs must be present and unique';
  END IF;
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements(p_matches) item
    WHERE (item->>'tournament_id')::uuid IS DISTINCT FROM p_tournament_id
  ) THEN
    RAISE EXCEPTION 'Every match must belong to this tournament';
  END IF;
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements(p_boards) item
    WHERE NOT EXISTS (
      SELECT 1 FROM jsonb_array_elements(p_matches) m
      WHERE (m->>'id')::uuid = (item->>'match_id')::uuid
    )
  ) THEN
    RAISE EXCEPTION 'Every board must belong to a new match';
  END IF;
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements(p_links) item
    WHERE NOT EXISTS (
      SELECT 1 FROM jsonb_array_elements(p_matches) m
      WHERE (m->>'id')::uuid = (item->>'id')::uuid
    ) OR NOT EXISTS (
      SELECT 1 FROM jsonb_array_elements(p_matches) m
      WHERE (m->>'id')::uuid = (item->>'next_match_id')::uuid
    ) OR item->>'next_match_slot' IS NULL
      OR item->>'next_match_slot' NOT IN ('player1', 'player2')
  ) THEN
    RAISE EXCEPTION 'Each bracket link needs two new matches and a valid slot';
  END IF;
  IF (SELECT count(DISTINCT (item->>'id')::uuid)
      FROM jsonb_array_elements(p_links) item) <> v_link_count THEN
    RAISE EXCEPTION 'Bracket link source IDs must be unique';
  END IF;
  IF EXISTS (
    SELECT 1 FROM jsonb_to_recordset(p_links) AS x(
      id uuid, next_match_id uuid, next_match_slot text
    )
    WHERE x.id = x.next_match_id
  ) OR EXISTS (
    SELECT 1 FROM jsonb_to_recordset(p_links) AS x(
      id uuid, next_match_id uuid, next_match_slot text
    )
    GROUP BY x.next_match_id, x.next_match_slot HAVING count(*) > 1
  ) THEN
    RAISE EXCEPTION 'A match cannot feed itself or share a destination slot';
  END IF;

  PERFORM 1 FROM public.matches WHERE tournament_id = p_tournament_id FOR UPDATE;
  PERFORM 1 FROM public.boards b
    JOIN public.matches m ON m.id = b.match_id
    WHERE m.tournament_id = p_tournament_id FOR UPDATE OF b;
  IF NOT COALESCE(p_force, false) AND (
    EXISTS (SELECT 1 FROM public.matches WHERE tournament_id = p_tournament_id
      AND (result_confirmed OR status IN ('live', 'paused', 'completed')
           OR winner_id IS NOT NULL))
    OR EXISTS (SELECT 1 FROM public.boards b
      JOIN public.matches m ON m.id = b.match_id
      WHERE m.tournament_id = p_tournament_id
        AND (b.status = 'completed' OR b.player1_score <> 0 OR b.player2_score <> 0))
  ) THEN
    RAISE EXCEPTION 'This draw has play or results; pass force to discard them';
  END IF;

  DELETE FROM public.matches WHERE tournament_id = p_tournament_id;

  INSERT INTO public.matches (
    id, tournament_id, match_number, round_name, round_index, stage, type,
    player1_id, player2_id, player1_name, player2_name, board_number,
    status, max_boards, target_points, bracket_position, number_of_sets
  )
  SELECT id, p_tournament_id, match_number, round_name, round_index, stage, type,
    player1_id, player2_id, player1_name, player2_name, board_number,
    COALESCE(status, 'scheduled'), max_boards, target_points,
    bracket_position, COALESCE(number_of_sets, 1)
  FROM jsonb_to_recordset(p_matches) AS x(
    id uuid, tournament_id uuid, match_number integer, round_name text,
    round_index integer, stage text, type text, player1_id uuid,
    player2_id uuid, player1_name text, player2_name text,
    board_number integer, status text, max_boards integer,
    target_points integer, bracket_position jsonb, number_of_sets integer
  );

  INSERT INTO public.boards (
    match_id, board_number, status, player1_score, player2_score, set_number
  )
  SELECT match_id, board_number, COALESCE(status, 'pending'),
    COALESCE(player1_score, 0), COALESCE(player2_score, 0),
    COALESCE(set_number, 1)
  FROM jsonb_to_recordset(p_boards) AS x(
    match_id uuid, board_number integer, status text,
    player1_score integer, player2_score integer, set_number integer
  );

  UPDATE public.matches m
  SET next_match_id = x.next_match_id, next_match_slot = x.next_match_slot
  FROM jsonb_to_recordset(p_links) AS x(
    id uuid, next_match_id uuid, next_match_slot text
  )
  WHERE m.id = x.id AND m.tournament_id = p_tournament_id;
  GET DIAGNOSTICS v_updated = ROW_COUNT;
  IF v_updated <> v_link_count THEN
    RAISE EXCEPTION 'Bracket link count mismatch';
  END IF;

  UPDATE public.tournaments
  SET fixtures_generated = true, schedule_published = false
  WHERE id = p_tournament_id;
  RETURN v_match_count;
END;
$$;

CREATE OR REPLACE FUNCTION public.update_match_schedule_batch(
  p_tournament_id uuid,
  p_rows jsonb
)
RETURNS integer
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
  v_tournament public.tournaments%ROWTYPE;
  v_count integer;
  v_updated integer;
BEGIN
  SELECT * INTO v_tournament FROM public.tournaments
  WHERE id = p_tournament_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Tournament % does not exist', p_tournament_id;
  END IF;
  IF v_tournament.status IN ('completed', 'cancelled') THEN
    RAISE EXCEPTION 'A completed or cancelled tournament cannot be rescheduled';
  END IF;
  IF jsonb_typeof(p_rows) IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION 'Schedule rows must be a JSON array';
  END IF;
  v_count := jsonb_array_length(p_rows);
  IF v_count = 0 OR v_count <> (
    SELECT count(*) FROM public.matches WHERE tournament_id = p_tournament_id
  ) THEN
    RAISE EXCEPTION 'The schedule must include every match exactly once';
  END IF;
  IF (SELECT count(DISTINCT (item->>'id')::uuid)
      FROM jsonb_array_elements(p_rows) item) <> v_count THEN
    RAISE EXCEPTION 'Schedule match IDs must be present and unique';
  END IF;
  IF EXISTS (
    SELECT 1 FROM jsonb_to_recordset(p_rows) AS x(
      id uuid, board_number integer, scheduled_date date, scheduled_time text
    )
    WHERE x.board_number IS NULL OR x.board_number < 1
      OR x.board_number > v_tournament.number_of_boards
      OR x.scheduled_date IS NULL
      OR x.scheduled_date < v_tournament.tournament_start_date
      OR x.scheduled_date > v_tournament.tournament_end_date
      OR x.scheduled_time IS NULL
      OR x.scheduled_time !~ '^([1-9]|1[0-2]):[0-5][0-9] (AM|PM)$'
  ) THEN
    RAISE EXCEPTION 'Schedule board, date, or time is outside tournament bounds';
  END IF;

  UPDATE public.matches m
  SET board_number = x.board_number,
      scheduled_date = x.scheduled_date,
      scheduled_time = x.scheduled_time
  FROM jsonb_to_recordset(p_rows) AS x(
    id uuid, board_number integer, scheduled_date date, scheduled_time text
  )
  WHERE m.id = x.id AND m.tournament_id = p_tournament_id;
  GET DIAGNOSTICS v_updated = ROW_COUNT;
  IF v_updated <> v_count THEN
    RAISE EXCEPTION 'Schedule contains a match outside this tournament';
  END IF;
  RETURN v_updated;
END;
$$;

-- RPCs live in public for PostgREST discovery but use caller privileges and
-- are executable only by the backend service role.
REVOKE ALL ON FUNCTION public.replace_tournament_fixtures(
  uuid, jsonb, jsonb, jsonb, boolean
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.replace_tournament_fixtures(
  uuid, jsonb, jsonb, jsonb, boolean
) TO service_role;
REVOKE ALL ON FUNCTION public.update_match_schedule_batch(
  uuid, jsonb
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.update_match_schedule_batch(
  uuid, jsonb
) TO service_role;
