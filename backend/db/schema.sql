-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. Profiles (syncs with Supabase Auth users)
CREATE TABLE IF NOT EXISTS public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    avatar TEXT,
    club TEXT DEFAULT 'Independent',
    city TEXT,
    rating INTEGER DEFAULT 1500 CHECK (rating >= 0),
    phone TEXT,
    email TEXT,
    role TEXT NOT NULL DEFAULT 'player' CHECK (role IN ('player', 'admin')),
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Indexing for profile lookups
CREATE INDEX IF NOT EXISTS idx_profiles_role ON public.profiles(role);
CREATE INDEX IF NOT EXISTS idx_profiles_rating ON public.profiles(rating DESC);

-- 2. Tournaments
CREATE TABLE IF NOT EXISTS public.tournaments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    description TEXT,
    category TEXT NOT NULL DEFAULT 'singles' CHECK (category IN ('singles', 'doubles', 'both')),
    format TEXT NOT NULL DEFAULT 'knockout' CHECK (format IN ('round_robin', 'knockout', 'league_knockout')),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'registration_open', 'registration_closed', 'scheduled', 'ongoing', 'completed')),
    registration_start_date DATE NOT NULL,
    registration_end_date DATE NOT NULL,
    tournament_start_date DATE NOT NULL,
    tournament_end_date DATE NOT NULL,
    venue TEXT NOT NULL,
    city TEXT NOT NULL,
    number_of_boards INTEGER NOT NULL DEFAULT 1 CHECK (number_of_boards > 0),
    entry_fee NUMERIC DEFAULT 0.0 CHECK (entry_fee >= 0),
    prize_pool TEXT,
    rules JSONB NOT NULL,
    poster_config JSONB,
    schedule_published BOOLEAN DEFAULT false NOT NULL,
    fixtures_generated BOOLEAN DEFAULT false NOT NULL,
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
    published_at TIMESTAMPTZ
);

-- Indexing for active/ongoing tournament searches
CREATE INDEX IF NOT EXISTS idx_tournaments_status ON public.tournaments(status);
CREATE INDEX IF NOT EXISTS idx_tournaments_dates ON public.tournaments(tournament_start_date, tournament_end_date);

-- 3. Teams (For doubles tournaments)
CREATE TABLE IF NOT EXISTS public.teams (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    player1_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    player2_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    club TEXT,
    city TEXT,
    rating INTEGER DEFAULT 1500,
    seed INTEGER,
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT unique_players_in_team UNIQUE (player1_id, player2_id)
);

-- 4. Registrations
CREATE TABLE IF NOT EXISTS public.registrations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tournament_id UUID NOT NULL REFERENCES public.tournaments(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('singles', 'doubles')),
    player_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    team_id UUID REFERENCES public.teams(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    registered_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
    payment_status TEXT NOT NULL DEFAULT 'pending' CHECK (payment_status IN ('paid', 'waived', 'pending')),
    notes TEXT,
    CONSTRAINT check_participant CHECK (
        (type = 'singles' AND player_id IS NOT NULL AND team_id IS NULL) OR
        (type = 'doubles' AND team_id IS NOT NULL AND player_id IS NULL)
    ),
    CONSTRAINT unique_player_registration UNIQUE (tournament_id, player_id),
    CONSTRAINT unique_team_registration UNIQUE (tournament_id, team_id)
);

CREATE INDEX IF NOT EXISTS idx_registrations_tournament ON public.registrations(tournament_id, status);

-- 5. Matches
CREATE TABLE IF NOT EXISTS public.matches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tournament_id UUID NOT NULL REFERENCES public.tournaments(id) ON DELETE CASCADE,
    match_number INTEGER NOT NULL,
    round_name TEXT NOT NULL,
    round_index INTEGER NOT NULL,
    stage TEXT NOT NULL CHECK (stage IN ('league', 'knockout')),
    type TEXT NOT NULL CHECK (type IN ('singles', 'doubles')),
    player1_id UUID, -- Can point to profiles.id (singles) or teams.id (doubles)
    player2_id UUID,
    player1_name TEXT NOT NULL DEFAULT 'Winner TBD',
    player2_name TEXT NOT NULL DEFAULT 'Winner TBD',
    board_number INTEGER NOT NULL DEFAULT 1,
    scheduled_date DATE,
    scheduled_time TEXT,
    status TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled', 'live', 'paused', 'completed')),
    timer_started_at BIGINT,
    timer_elapsed_seconds INTEGER DEFAULT 0 NOT NULL,
    is_timer_running BOOLEAN DEFAULT false NOT NULL,
    match_completed_at TIMESTAMPTZ,
    max_boards INTEGER NOT NULL DEFAULT 3 CHECK (max_boards > 0),
    target_points INTEGER DEFAULT 25,
    winner_id UUID,
    winner_name TEXT,
    result_confirmed BOOLEAN DEFAULT false NOT NULL,
    result_confirmed_at TIMESTAMPTZ,
    player1_board_wins INTEGER DEFAULT 0 NOT NULL,
    player2_board_wins INTEGER DEFAULT 0 NOT NULL,
    player1_total_points INTEGER DEFAULT 0 NOT NULL,
    player2_total_points INTEGER DEFAULT 0 NOT NULL,
    next_match_id UUID REFERENCES public.matches(id) ON DELETE SET NULL,
    next_match_slot TEXT CHECK (next_match_slot IN ('player1', 'player2')),
    bracket_position JSONB, -- { "round": 1, "matchIndex": 0 }
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_matches_tournament ON public.matches(tournament_id);
CREATE INDEX IF NOT EXISTS idx_matches_status ON public.matches(status);
CREATE INDEX IF NOT EXISTS idx_matches_players ON public.matches(player1_id, player2_id);

-- 6. Boards (Individual Board Scores for Live Match updates)
CREATE TABLE IF NOT EXISTS public.boards (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    match_id UUID NOT NULL REFERENCES public.matches(id) ON DELETE CASCADE,
    board_number INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'completed')),
    player1_score INTEGER DEFAULT 0 NOT NULL,
    player2_score INTEGER DEFAULT 0 NOT NULL,
    queen_claimed_by TEXT DEFAULT 'none' CHECK (queen_claimed_by IN ('player1', 'player2', 'none')),
    queen_covered BOOLEAN DEFAULT false NOT NULL,
    fouls_player1 INTEGER DEFAULT 0 NOT NULL,
    fouls_player2 INTEGER DEFAULT 0 NOT NULL,
    white_coins_pocketed INTEGER DEFAULT 0 NOT NULL,
    black_coins_pocketed INTEGER DEFAULT 0 NOT NULL,
    duration_minutes NUMERIC,
    completed_at TIMESTAMPTZ,
    notes TEXT,
    CONSTRAINT unique_board_per_match UNIQUE (match_id, board_number)
);

CREATE INDEX IF NOT EXISTS idx_boards_match ON public.boards(match_id);

-- 7. Score Audit Logs
CREATE TABLE IF NOT EXISTS public.score_audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    match_id UUID NOT NULL REFERENCES public.matches(id) ON DELETE CASCADE,
    admin_id UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    admin_name TEXT NOT NULL,
    board_number INTEGER NOT NULL,
    previous_score JSONB NOT NULL,
    new_score JSONB NOT NULL,
    reason TEXT NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- 8. Notifications
CREATE TABLE IF NOT EXISTS public.notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE, -- NULL means public announcement
    tournament_id UUID REFERENCES public.tournaments(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    read BOOLEAN DEFAULT false NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('tournament_published', 'registration_confirmed', 'registration_closed', 'schedule_published', 'match_approaching', 'result_confirmed', 'knockout_advanced')),
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notifications_recipient ON public.notifications(profile_id, read);

-- 9. Administrative Audit Logs
CREATE TABLE IF NOT EXISTS public.audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    previous_state JSONB,
    new_state JSONB,
    timestamp TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
    request_context JSONB
);
