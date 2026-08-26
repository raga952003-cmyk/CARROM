-- =============================================================================
-- 002 — Serverless architecture support
--
-- Adds what the architecture spec requires but the initial schema did not have:
--   * the full tournament / match state vocabularies (spec 75, 76)
--   * transactional RPCs for score + result application (spec 71, 77)
--   * idempotency keys for repeatable critical operations (spec 79)
--   * append-only protection for audit logs (spec 83)
--   * Realtime publication so clients stop polling (spec 72, 91)
--
-- Safe to re-run.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 0. Preflight: make sure this is the right database
--
-- This migration alters tables created by schema.sql. Running it against a
-- project where that has not been applied fails with a bare
-- "42P01: relation public.tournaments does not exist", which does not say why.
-- The check below fails loudly with an actionable message instead.
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    missing TEXT[] := ARRAY[]::TEXT[];
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'profiles', 'tournaments', 'teams', 'registrations',
        'matches', 'boards', 'score_audit_logs', 'notifications', 'audit_logs'
    ] LOOP
        IF to_regclass('public.' || t) IS NULL THEN
            missing := array_append(missing, t);
        END IF;
    END LOOP;

    IF array_length(missing, 1) > 0 THEN
        RAISE EXCEPTION
            'Base schema missing in this database (current_database=%, tables absent: %). '
            'You are probably connected to the wrong Supabase project. '
            'Run db/schema.sql and db/triggers_and_security.sql first, '
            'or switch to the project whose ref matches SUPABASE_URL in backend/.env.',
            current_database(), array_to_string(missing, ', ');
    END IF;

    RAISE NOTICE 'Preflight OK: base schema present in %', current_database();
END $$;

-- -----------------------------------------------------------------------------
-- 1. Tournament + match state vocabularies
-- -----------------------------------------------------------------------------
-- 'scheduled' and 'ongoing' are kept as legacy synonyms of 'fixture_published'
-- and 'in_progress' so existing rows stay valid.
ALTER TABLE public.tournaments DROP CONSTRAINT IF EXISTS tournaments_status_check;
ALTER TABLE public.tournaments ADD CONSTRAINT tournaments_status_check
  CHECK (status IN (
    'draft',
    'registration_open',
    'registration_closed',
    'fixture_generation',
    'fixture_published',
    'in_progress',
    'completed',
    'cancelled',
    'scheduled',
    'ongoing'
  ));

ALTER TABLE public.matches DROP CONSTRAINT IF EXISTS matches_status_check;
ALTER TABLE public.matches ADD CONSTRAINT matches_status_check
  CHECK (status IN (
    'scheduled',
    'ready',
    'live',
    'paused',
    'completed',
    'cancelled',
    'postponed'
  ));

-- -----------------------------------------------------------------------------
-- 2. Idempotency keys (spec 79)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.idempotency_keys (
    key TEXT PRIMARY KEY,
    endpoint TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status_code INTEGER,
    response JSONB,
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_idempotency_created ON public.idempotency_keys(created_at);
ALTER TABLE public.idempotency_keys ENABLE ROW LEVEL SECURITY;
-- No policies: only the service role (which bypasses RLS) may touch this table.

-- -----------------------------------------------------------------------------
-- 3. Authorisation helper for the RPCs below
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.is_admin_or_service()
RETURNS BOOLEAN AS $$
  SELECT COALESCE(auth.jwt() ->> 'role' = 'service_role', false)
      OR COALESCE(auth.jwt() -> 'app_metadata' ->> 'role' = 'admin', false);
$$ LANGUAGE sql STABLE SECURITY DEFINER;

-- -----------------------------------------------------------------------------
-- 4. apply_board_result — one transaction for a board score submission
--
-- The deterministic scoring maths stays in the Python engine (spec 68/70); this
-- function exists so the resulting writes land atomically (spec 71/77):
-- board row, audit row, match aggregates and next-board activation either all
-- commit or none do.
-- -----------------------------------------------------------------------------
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
    v_prev public.boards%ROWTYPE;
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

    UPDATE public.boards SET
        player1_score        = COALESCE((p_board_patch ->> 'player1_score')::INTEGER, player1_score),
        player2_score        = COALESCE((p_board_patch ->> 'player2_score')::INTEGER, player2_score),
        status               = COALESCE(p_board_patch ->> 'status', status),
        queen_claimed_by     = COALESCE(p_board_patch ->> 'queen_claimed_by', queen_claimed_by),
        queen_covered        = COALESCE((p_board_patch ->> 'queen_covered')::BOOLEAN, queen_covered),
        fouls_player1        = COALESCE((p_board_patch ->> 'fouls_player1')::INTEGER, fouls_player1),
        fouls_player2        = COALESCE((p_board_patch ->> 'fouls_player2')::INTEGER, fouls_player2),
        white_coins_pocketed = COALESCE((p_board_patch ->> 'white_coins_pocketed')::INTEGER, white_coins_pocketed),
        black_coins_pocketed = COALESCE((p_board_patch ->> 'black_coins_pocketed')::INTEGER, black_coins_pocketed),
        notes                = COALESCE(p_board_patch ->> 'notes', notes),
        completed_at         = COALESCE((p_board_patch ->> 'completed_at')::TIMESTAMPTZ, completed_at)
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
        match_completed_at   = NULLIF(p_match_patch ->> 'match_completed_at', '')::TIMESTAMPTZ
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

-- -----------------------------------------------------------------------------
-- 5. confirm_match_result — the spec-71 completion transaction
--
-- Marks the result confirmed, advances the winner into the next bracket slot,
-- writes the audit record and delivers notifications, all atomically.
-- Idempotent: confirming an already-confirmed match is a no-op.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.confirm_match_result(
    p_match_id UUID,
    p_actor_id UUID,
    p_actor_name TEXT,
    p_notifications JSONB DEFAULT '[]'::JSONB
)
RETURNS JSONB AS $$
DECLARE
    v_match public.matches%ROWTYPE;
    v_before JSONB;
    v_advanced BOOLEAN := false;
BEGIN
    IF NOT public.is_admin_or_service() THEN
        RAISE EXCEPTION 'insufficient_privilege: admin rights required to confirm a result';
    END IF;

    SELECT * INTO v_match FROM public.matches WHERE id = p_match_id FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'match_not_found: %', p_match_id;
    END IF;

    -- Idempotency (spec 79): a repeated confirm must not re-advance the winner
    -- or re-send notifications.
    IF v_match.result_confirmed THEN
        RETURN jsonb_build_object(
            'confirmed', true, 'advanced', false, 'already_confirmed', true,
            'match_id', p_match_id
        );
    END IF;

    v_before := to_jsonb(v_match);

    UPDATE public.matches
    SET result_confirmed = true,
        result_confirmed_at = timezone('utc'::text, now()),
        status = 'completed'
    WHERE id = p_match_id
    RETURNING * INTO v_match;

    IF v_match.next_match_id IS NOT NULL AND v_match.winner_id IS NOT NULL THEN
        IF v_match.next_match_slot = 'player1' THEN
            UPDATE public.matches
            SET player1_id = v_match.winner_id, player1_name = v_match.winner_name
            WHERE id = v_match.next_match_id;
        ELSE
            UPDATE public.matches
            SET player2_id = v_match.winner_id, player2_name = v_match.winner_name
            WHERE id = v_match.next_match_id;
        END IF;
        v_advanced := true;
    END IF;

    IF jsonb_array_length(p_notifications) > 0 THEN
        INSERT INTO public.notifications (profile_id, tournament_id, title, message, type, read)
        SELECT NULLIF(n ->> 'profile_id', '')::UUID,
               NULLIF(n ->> 'tournament_id', '')::UUID,
               n ->> 'title',
               n ->> 'message',
               n ->> 'type',
               false
        FROM jsonb_array_elements(p_notifications) AS n;
    END IF;

    INSERT INTO public.audit_logs (
        user_id, action, entity_type, entity_id, previous_state, new_state, request_context
    ) VALUES (
        p_actor_id, 'match.confirm_result', 'match', p_match_id::TEXT,
        v_before, to_jsonb(v_match),
        jsonb_build_object('actor_name', p_actor_name, 'advanced', v_advanced)
    );

    RETURN jsonb_build_object(
        'confirmed', true, 'advanced', v_advanced, 'already_confirmed', false,
        'match_id', p_match_id, 'winner_id', v_match.winner_id
    );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- -----------------------------------------------------------------------------
-- 6. Audit logs are append-only (spec 83)
-- -----------------------------------------------------------------------------
REVOKE UPDATE, DELETE ON public.audit_logs FROM anon, authenticated;
REVOKE UPDATE, DELETE ON public.score_audit_logs FROM anon, authenticated;

-- -----------------------------------------------------------------------------
-- 6b. Re-assert the public read policies for matches and boards.
--
-- Realtime only delivers a change event for a row the subscriber is allowed to
-- SELECT, so without these the live stream is silently empty. They are declared
-- in triggers_and_security.sql too; repeated here because a database was found
-- in production missing exactly these two.
-- -----------------------------------------------------------------------------
DROP POLICY IF EXISTS select_matches ON public.matches;
CREATE POLICY select_matches ON public.matches FOR SELECT TO public USING (true);

DROP POLICY IF EXISTS select_boards ON public.boards;
CREATE POLICY select_boards ON public.boards FOR SELECT TO public USING (true);

DROP POLICY IF EXISTS select_registrations ON public.registrations;
CREATE POLICY select_registrations ON public.registrations FOR SELECT TO public USING (true);

DROP POLICY IF EXISTS select_teams ON public.teams;
CREATE POLICY select_teams ON public.teams FOR SELECT TO public USING (true);

DROP POLICY IF EXISTS select_tournaments ON public.tournaments;
CREATE POLICY select_tournaments ON public.tournaments FOR SELECT TO public USING (true);

-- -----------------------------------------------------------------------------
-- 7. Realtime publication (spec 72) — replaces client polling (spec 91)
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'tournaments', 'registrations', 'matches', 'boards', 'notifications'
    ] LOOP
        BEGIN
            EXECUTE format('ALTER PUBLICATION supabase_realtime ADD TABLE public.%I', t);
        EXCEPTION
            WHEN duplicate_object THEN NULL;   -- already published
            WHEN undefined_object THEN NULL;   -- publication absent (self-hosted)
        END;
    END LOOP;
END $$;

-- Realtime payloads need the full old row to compute diffs on updates.
ALTER TABLE public.matches REPLICA IDENTITY FULL;
ALTER TABLE public.boards REPLICA IDENTITY FULL;

-- -----------------------------------------------------------------------------
-- 8. Indexes for the standings / leaderboard queries (spec 89)
-- -----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_matches_standings
    ON public.matches(tournament_id, stage, result_confirmed);
CREATE INDEX IF NOT EXISTS idx_audit_logs_entity
    ON public.audit_logs(entity_type, entity_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_user
    ON public.audit_logs(user_id, timestamp DESC);
