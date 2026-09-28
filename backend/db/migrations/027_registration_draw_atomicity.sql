-- Apply after 026. Freeze the registration roster at the same database lock
-- that swaps the draw. The API supplies the complete approved roster it used
-- to build fixtures; an entry, eligibility, or participant change between
-- its reads and this transaction makes the draw fail instead of omitting or
-- misplacing that entrant.

CREATE OR REPLACE FUNCTION public.guard_registration_roster_after_draw()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
  v_tournament public.tournaments%ROWTYPE;
  v_old_id uuid;
  v_new_id uuid;
  v_roster_change boolean;
BEGIN
  IF TG_OP = 'DELETE' THEN
    v_old_id := OLD.tournament_id;
    v_roster_change := true;
  ELSIF TG_OP = 'INSERT' THEN
    v_new_id := NEW.tournament_id;
    v_roster_change := true;
  ELSE
    v_old_id := OLD.tournament_id;
    v_new_id := NEW.tournament_id;
    v_roster_change := OLD.tournament_id IS DISTINCT FROM NEW.tournament_id
      OR OLD.type IS DISTINCT FROM NEW.type
      OR OLD.player_id IS DISTINCT FROM NEW.player_id
      OR OLD.team_id IS DISTINCT FROM NEW.team_id
      OR OLD.status IS DISTINCT FROM NEW.status
      OR OLD.fee_paise IS DISTINCT FROM NEW.fee_paise;
    -- Payment settlement or a refund can alter payment_status without
    -- changing a drawn entrant. Lock it too: the draw checks paid eligibility.
    IF NOT v_roster_change
       AND OLD.payment_status IS NOT DISTINCT FROM NEW.payment_status THEN
      RETURN NEW;
    END IF;
  END IF;

  -- The same lock order as replace_tournament_fixtures_checked. For a move
  -- between tournaments, lock the two parent rows in UUID order.
  FOR v_tournament IN
    SELECT * FROM public.tournaments
    WHERE id IN (v_old_id, v_new_id)
    ORDER BY id FOR UPDATE
  LOOP
    IF v_roster_change AND (v_tournament.fixtures_generated OR EXISTS (
      SELECT 1 FROM public.matches m
      WHERE m.tournament_id = v_tournament.id
    )) THEN
      RAISE EXCEPTION 'Fixtures already exist; use match walkover or correction rather than changing the registered roster';
    END IF;
  END LOOP;

  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$;

-- Both are BEFORE UPDATE triggers. PostgreSQL fires same-kind triggers by
-- name, so zz_ runs after auto_approve_settled_registration and sees its
-- resulting NEW.status. Payment-status-only refunds remain allowed.
DROP TRIGGER IF EXISTS zz_guard_registration_roster_after_draw
  ON public.registrations;
CREATE TRIGGER zz_guard_registration_roster_after_draw
  BEFORE INSERT OR UPDATE OR DELETE ON public.registrations
  FOR EACH ROW EXECUTE FUNCTION public.guard_registration_roster_after_draw();

REVOKE ALL ON FUNCTION public.guard_registration_roster_after_draw()
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.guard_registration_roster_after_draw()
  TO service_role;

CREATE OR REPLACE FUNCTION public.replace_tournament_fixtures_checked(
  p_tournament_id uuid,
  p_matches jsonb,
  p_boards jsonb,
  p_links jsonb,
  p_approved_roster jsonb,
  p_tournament_snapshot jsonb,
  p_force boolean DEFAULT false
)
RETURNS integer
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
  v_tournament public.tournaments%ROWTYPE;
  v_current_roster jsonb;
BEGIN
  SELECT * INTO v_tournament FROM public.tournaments
  WHERE id = p_tournament_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Tournament % does not exist', p_tournament_id;
  END IF;
  IF v_tournament.status NOT IN (
    'registration_closed', 'fixture_generation', 'fixture_published',
    'scheduled', 'in_progress', 'ongoing'
  ) THEN
    RAISE EXCEPTION 'Close registration before generating fixtures';
  END IF;
  IF p_tournament_snapshot IS DISTINCT FROM jsonb_build_object(
    'status', v_tournament.status,
    'fixtures_generated', v_tournament.fixtures_generated,
    'format', v_tournament.format,
    'category', v_tournament.category,
    'rules', v_tournament.rules,
    'number_of_boards', v_tournament.number_of_boards
  ) THEN
    RAISE EXCEPTION 'Tournament draw settings changed; reload and retry';
  END IF;
  IF jsonb_typeof(p_approved_roster) IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION 'The approved registration snapshot is missing or invalid';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.registrations r
    WHERE r.tournament_id = p_tournament_id AND r.status = 'pending'
  ) THEN
    RAISE EXCEPTION 'Resolve every pending registration before generating fixtures';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.registrations r
    WHERE r.tournament_id = p_tournament_id AND r.status = 'approved'
      AND COALESCE(r.fee_paise,
        round(COALESCE(v_tournament.entry_fee, 0) * 100)::integer) > 0
      AND COALESCE(r.payment_status, 'pending') NOT IN ('paid', 'waived')
  ) THEN
    RAISE EXCEPTION 'Resolve every unpaid approved registration before generating fixtures';
  END IF;
  SELECT COALESCE(jsonb_agg(
    jsonb_build_object(
      'id', r.id::text, 'type', r.type,
      'player_id', r.player_id::text, 'team_id', r.team_id::text,
      'payment_status', r.payment_status, 'fee_paise', r.fee_paise
    ) ORDER BY r.id
  ), '[]'::jsonb) INTO v_current_roster
  FROM public.registrations r
  WHERE r.tournament_id = p_tournament_id AND r.status = 'approved';
  IF v_current_roster IS DISTINCT FROM p_approved_roster THEN
    RAISE EXCEPTION 'The approved entry list changed during the draw; reload and retry';
  END IF;

  -- The existing RPC is atomic and takes the same tournament lock. Calling it
  -- here keeps its match/board/link validation and replacement in this txn.
  RETURN public.replace_tournament_fixtures(
    p_tournament_id, p_matches, p_boards, p_links, p_force
  );
END;
$$;

REVOKE ALL ON FUNCTION public.replace_tournament_fixtures_checked(
  uuid, jsonb, jsonb, jsonb, jsonb, jsonb, boolean
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.replace_tournament_fixtures_checked(
  uuid, jsonb, jsonb, jsonb, jsonb, jsonb, boolean
) TO service_role;

-- The health route calls this without touching any entry or match. Both the
-- wrapper RPC and the enabled trigger must be present before it returns true.
CREATE OR REPLACE FUNCTION public.registration_draw_atomicity_ready()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
  SELECT pg_catalog.to_regprocedure(
      'public.replace_tournament_fixtures_checked(uuid,jsonb,jsonb,jsonb,jsonb,jsonb,boolean)'
    ) IS NOT NULL
    AND EXISTS (
      SELECT 1 FROM pg_catalog.pg_trigger tg
      WHERE tg.tgrelid = 'public.registrations'::regclass
        AND tg.tgname = 'zz_guard_registration_roster_after_draw'
        AND tg.tgenabled IN ('O', 'A')
        AND tg.tgfoid = pg_catalog.to_regprocedure(
          'public.guard_registration_roster_after_draw()'
        )
    );
$$;

REVOKE ALL ON FUNCTION public.registration_draw_atomicity_ready()
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.registration_draw_atomicity_ready()
  TO service_role;
