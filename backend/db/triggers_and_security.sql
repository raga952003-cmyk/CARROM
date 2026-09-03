-- =============================================================================
-- SUPERSEDED by db/migrations/013_profiles_trigger_and_rls.sql.
-- Kept for reference only. Do not run this file; run 013.
--
-- This file sat outside the numbered migrations, which is how a project ended
-- up with every migration applied and no handle_new_user trigger. 013 is the
-- same trigger, the same RLS switches and the same policies in the numbered,
-- idempotent form the rest of the migrations take, so it is applied and
-- re-applied the same way they are and /api/health can account for it.
--
-- One line below is now actively wrong: select_profiles reads every column of
-- every profile to anyone holding the anon key. Migration 011 closed that, and
-- running this file after 011 would reopen it. 013 carries 011's policy.
-- =============================================================================

-- Sync user registration from Supabase Auth to Profiles
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger AS $$
BEGIN
  INSERT INTO public.profiles (id, name, email, role, rating)
  VALUES (
    new.id,
    COALESCE(new.raw_user_meta_data->>'name', 'User'),
    new.email,
    COALESCE(new.raw_app_meta_data->>'role', new.raw_user_meta_data->>'role', 'player'),
    COALESCE((new.raw_user_meta_data->>'rating')::integer, 1500)
  );
  RETURN new;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Recreate trigger if exists
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- Enable Row-Level Security (RLS)
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tournaments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.teams ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.registrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.matches ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.boards ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.score_audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY;

-- Helper function to check if active user is admin using JWT app_metadata
CREATE OR REPLACE FUNCTION public.is_admin()
RETURNS BOOLEAN AS $$
  SELECT COALESCE(auth.jwt() -> 'app_metadata' ->> 'role' = 'admin', false);
$$ LANGUAGE sql SECURITY DEFINER;

-- RLS Policies

-- Drop existing policies if they exist to allow re-running
DROP POLICY IF EXISTS select_profiles ON public.profiles;
DROP POLICY IF EXISTS update_profiles_self ON public.profiles;
DROP POLICY IF EXISTS admin_all_profiles ON public.profiles;

DROP POLICY IF EXISTS select_tournaments ON public.tournaments;
DROP POLICY IF EXISTS admin_all_tournaments ON public.tournaments;

DROP POLICY IF EXISTS select_teams ON public.teams;
DROP POLICY IF EXISTS insert_teams_member ON public.teams;
DROP POLICY IF EXISTS admin_all_teams ON public.teams;

DROP POLICY IF EXISTS select_registrations ON public.registrations;
DROP POLICY IF EXISTS insert_registrations_self ON public.registrations;
DROP POLICY IF EXISTS admin_all_registrations ON public.registrations;

DROP POLICY IF EXISTS select_matches ON public.matches;
DROP POLICY IF EXISTS admin_all_matches ON public.matches;

DROP POLICY IF EXISTS select_boards ON public.boards;
DROP POLICY IF EXISTS admin_all_boards ON public.boards;

DROP POLICY IF EXISTS select_score_audit ON public.score_audit_logs;
DROP POLICY IF EXISTS admin_insert_score_audit ON public.score_audit_logs;

DROP POLICY IF EXISTS select_notifications ON public.notifications;
DROP POLICY IF EXISTS update_own_notifications ON public.notifications;
DROP POLICY IF EXISTS admin_all_notifications ON public.notifications;

DROP POLICY IF EXISTS admin_select_audit_logs ON public.audit_logs;

-- Profiles Policies
CREATE POLICY select_profiles ON public.profiles FOR SELECT TO public USING (true);
CREATE POLICY update_profiles_self ON public.profiles FOR UPDATE USING (auth.uid() = id);
CREATE POLICY admin_all_profiles ON public.profiles FOR ALL USING (public.is_admin());

-- Tournaments Policies
CREATE POLICY select_tournaments ON public.tournaments FOR SELECT TO public USING (true);
CREATE POLICY admin_all_tournaments ON public.tournaments FOR ALL USING (public.is_admin());

-- Teams Policies
CREATE POLICY select_teams ON public.teams FOR SELECT TO public USING (true);
CREATE POLICY insert_teams_member ON public.teams FOR INSERT WITH CHECK (auth.uid() = player1_id OR auth.uid() = player2_id);
CREATE POLICY admin_all_teams ON public.teams FOR ALL USING (public.is_admin());

-- Registrations Policies
CREATE POLICY select_registrations ON public.registrations FOR SELECT TO public USING (true);
CREATE POLICY insert_registrations_self ON public.registrations FOR INSERT WITH CHECK (
  auth.uid() = player_id OR 
  EXISTS (SELECT 1 FROM public.teams WHERE id = team_id AND (player1_id = auth.uid() OR player2_id = auth.uid()))
);
CREATE POLICY admin_all_registrations ON public.registrations FOR ALL USING (public.is_admin());

-- Matches & Boards Policies
CREATE POLICY select_matches ON public.matches FOR SELECT TO public USING (true);
CREATE POLICY admin_all_matches ON public.matches FOR ALL USING (public.is_admin());

CREATE POLICY select_boards ON public.boards FOR SELECT TO public USING (true);
CREATE POLICY admin_all_boards ON public.boards FOR ALL USING (public.is_admin());

-- Score Audit Logs
CREATE POLICY select_score_audit ON public.score_audit_logs FOR SELECT TO public USING (true);
CREATE POLICY admin_insert_score_audit ON public.score_audit_logs FOR INSERT WITH CHECK (public.is_admin());

-- Notifications
CREATE POLICY select_notifications ON public.notifications FOR SELECT USING (profile_id IS NULL OR profile_id = auth.uid());
-- Without this, only admins could ever flip `read`, so "mark as read" silently
-- updated zero rows for every player.
CREATE POLICY update_own_notifications ON public.notifications FOR UPDATE
  USING (profile_id = auth.uid())
  WITH CHECK (profile_id = auth.uid());
CREATE POLICY admin_all_notifications ON public.notifications FOR ALL USING (public.is_admin());

-- Administrative Audit Logs
CREATE POLICY admin_select_audit_logs ON public.audit_logs FOR SELECT USING (public.is_admin());
