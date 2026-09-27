-- Apply after 016. The API owns all tournament and payment state transitions.
-- The browser may read published data but must not write state through PostgREST.

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger AS $$
BEGIN
  INSERT INTO public.profiles (id, name, email, role, rating)
  VALUES (
    NEW.id,
    COALESCE(NEW.raw_user_meta_data->>'name', 'User'),
    NEW.email,
    CASE WHEN NEW.raw_app_meta_data->>'role' = 'admin' THEN 'admin' ELSE 'player' END,
    COALESCE((NEW.raw_user_meta_data->>'rating')::integer, 1500)
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = '';

-- Keep direct reads limited to the rows a browser actually needs. The API
-- builds the public registration/score projection with its service role.
DROP POLICY IF EXISTS select_profiles ON public.profiles;
DROP POLICY IF EXISTS select_own_profile ON public.profiles;
CREATE POLICY select_own_profile ON public.profiles FOR SELECT TO authenticated
  USING (id = auth.uid());

DROP POLICY IF EXISTS select_tournaments ON public.tournaments;
CREATE POLICY select_tournaments ON public.tournaments FOR SELECT TO public
  USING (status <> 'draft' OR owner_id = auth.uid());

DROP POLICY IF EXISTS select_teams ON public.teams;
CREATE POLICY select_teams ON public.teams FOR SELECT TO authenticated
  USING (player1_id = auth.uid() OR player2_id = auth.uid());

DROP POLICY IF EXISTS select_registrations ON public.registrations;
CREATE POLICY select_registrations ON public.registrations FOR SELECT TO authenticated
  USING (
    player_id = auth.uid()
    OR EXISTS (
      SELECT 1 FROM public.teams t WHERE t.id = team_id
        AND (t.player1_id = auth.uid() OR t.player2_id = auth.uid())
    )
    OR EXISTS (
      SELECT 1 FROM public.tournaments t WHERE t.id = tournament_id
        AND t.owner_id = auth.uid()
    )
  );

-- Matches include unpublished date/time fields. A row cannot be partly
-- hidden by RLS, so direct Data API reads start at schedule publication.
DROP POLICY IF EXISTS select_matches ON public.matches;
CREATE POLICY select_matches ON public.matches FOR SELECT TO public
  USING (EXISTS (
    SELECT 1 FROM public.tournaments t WHERE t.id = tournament_id
      AND (t.schedule_published OR t.owner_id = auth.uid())
  ));
DROP POLICY IF EXISTS select_boards ON public.boards;
CREATE POLICY select_boards ON public.boards FOR SELECT TO public
  USING (EXISTS (
    SELECT 1 FROM public.matches m WHERE m.id = match_id
  ));

DROP POLICY IF EXISTS insert_own_access ON public.tournament_access;
CREATE POLICY insert_own_access ON public.tournament_access FOR INSERT
  WITH CHECK (
    user_id = auth.uid() AND status = 'pending' AND decided_at IS NULL
    AND decided_by IS NULL
  );

DROP POLICY IF EXISTS insert_registrations_self ON public.registrations;
CREATE POLICY insert_registrations_self ON public.registrations FOR INSERT
  WITH CHECK (
    status = 'pending' AND payment_status = 'pending'
    AND (auth.uid() = player_id OR EXISTS (
      SELECT 1 FROM public.teams WHERE id = team_id
        AND (player1_id = auth.uid() OR player2_id = auth.uid())
    ))
  );

-- RLS does not protect TRUNCATE. Revoke every inherited Data API privilege,
-- then grant only the reads (and notification read receipts) still in use.
-- Admin permissions in the API use the service role and tournament ownership.
REVOKE ALL ON TABLE
  public.profiles, public.tournaments, public.teams,
  public.registrations, public.matches, public.boards,
  public.tournament_access, public.payments,
  public.score_audit_logs, public.notifications, public.audit_logs
FROM PUBLIC, anon, authenticated;

GRANT SELECT ON TABLE public.tournaments,
  public.matches, public.boards TO anon, authenticated;
GRANT SELECT ON TABLE public.profiles, public.teams, public.registrations,
  public.tournament_access, public.payments, public.notifications
  TO authenticated;
GRANT UPDATE (read) ON TABLE public.notifications TO authenticated;

-- The sets and idempotency tables are introduced by earlier numbered
-- migrations, but older installations may lack one of them.
DO $$ BEGIN
  IF to_regclass('public.match_sets') IS NOT NULL THEN
    REVOKE ALL ON TABLE public.match_sets FROM PUBLIC, anon, authenticated;
    GRANT SELECT ON TABLE public.match_sets TO anon, authenticated;
    DROP POLICY IF EXISTS select_match_sets ON public.match_sets;
    CREATE POLICY select_match_sets ON public.match_sets FOR SELECT TO public
      USING (EXISTS (
        SELECT 1 FROM public.matches m WHERE m.id = match_id
      ));
  END IF;
  IF to_regclass('public.idempotency_keys') IS NOT NULL THEN
    REVOKE ALL ON TABLE public.idempotency_keys FROM PUBLIC, anon, authenticated;
  END IF;
END $$;

DROP POLICY IF EXISTS admin_all_profiles ON public.profiles;
DROP POLICY IF EXISTS admin_all_tournaments ON public.tournaments;
DROP POLICY IF EXISTS admin_all_teams ON public.teams;
DROP POLICY IF EXISTS admin_all_registrations ON public.registrations;
DROP POLICY IF EXISTS admin_all_matches ON public.matches;
DROP POLICY IF EXISTS admin_all_boards ON public.boards;
DROP POLICY IF EXISTS admin_all_notifications ON public.notifications;
DROP POLICY IF EXISTS admin_insert_score_audit ON public.score_audit_logs;

-- These older SECURITY DEFINER RPCs accept caller-supplied score and winner
-- patches. An admin JWT could invoke them directly for another owner's event
-- unless execution is limited to the API's service role.
DO $$ BEGIN
  IF to_regprocedure('public.apply_board_result(uuid,integer,jsonb,jsonb,jsonb,integer,integer)') IS NOT NULL THEN
    REVOKE ALL ON FUNCTION public.apply_board_result(
      uuid, integer, jsonb, jsonb, jsonb, integer, integer
    ) FROM PUBLIC, anon, authenticated;
    GRANT EXECUTE ON FUNCTION public.apply_board_result(
      uuid, integer, jsonb, jsonb, jsonb, integer, integer
    ) TO service_role;
  END IF;
  IF to_regprocedure('public.apply_board_result(uuid,integer,jsonb,jsonb,jsonb,integer)') IS NOT NULL THEN
    REVOKE ALL ON FUNCTION public.apply_board_result(
      uuid, integer, jsonb, jsonb, jsonb, integer
    ) FROM PUBLIC, anon, authenticated;
    GRANT EXECUTE ON FUNCTION public.apply_board_result(
      uuid, integer, jsonb, jsonb, jsonb, integer
    ) TO service_role;
  END IF;
  IF to_regprocedure('public.confirm_match_result(uuid,uuid,text,jsonb)') IS NOT NULL THEN
    REVOKE ALL ON FUNCTION public.confirm_match_result(
      uuid, uuid, text, jsonb
    ) FROM PUBLIC, anon, authenticated;
    GRANT EXECUTE ON FUNCTION public.confirm_match_result(
      uuid, uuid, text, jsonb
    ) TO service_role;
  END IF;
END $$;

DO $$ BEGIN
  RAISE NOTICE '017_secure_data_api applied: public writes revoked and profile role no longer trusts user metadata';
END $$;
