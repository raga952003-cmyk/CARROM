-- =============================================================================
-- 003 — Tournament ownership and delegated access
--
-- Until now any account with role='admin' could edit, re-draw or delete any
-- tournament, including ones created by someone else. This introduces an owner
-- per tournament and an explicit request/approve flow for anyone else who
-- needs to help run it.
--
-- Safe to re-run.
-- =============================================================================

DO $$
BEGIN
    IF to_regclass('public.tournaments') IS NULL THEN
        RAISE EXCEPTION
            'Base schema missing in this database (current_database=%). '
            'Run db/schema.sql and db/triggers_and_security.sql first, or switch '
            'to the project whose ref matches SUPABASE_URL in backend/.env.',
            current_database();
    END IF;
END $$;

-- -----------------------------------------------------------------------------
-- 1. Owner
-- -----------------------------------------------------------------------------
ALTER TABLE public.tournaments
    ADD COLUMN IF NOT EXISTS owner_id UUID REFERENCES public.profiles(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_tournaments_owner ON public.tournaments(owner_id);

-- Existing tournaments have no recorded creator. Adopt the earliest admin so
-- they are owned by somebody rather than being unclaimed forever.
UPDATE public.tournaments t
SET owner_id = (
    SELECT p.id FROM public.profiles p
    WHERE p.role = 'admin'
    ORDER BY p.created_at
    LIMIT 1
)
WHERE t.owner_id IS NULL;

-- -----------------------------------------------------------------------------
-- 2. Delegated access
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.tournament_access (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tournament_id UUID NOT NULL REFERENCES public.tournaments(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    -- manager: full control of this tournament. scorer: may run matches and
    -- enter scores, but not re-draw fixtures or change settings.
    access_role TEXT NOT NULL DEFAULT 'manager'
        CHECK (access_role IN ('manager', 'scorer')),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'revoked')),
    message TEXT,
    decision_note TEXT,
    requested_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
    decided_at TIMESTAMPTZ,
    decided_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    -- One row per person per tournament; a re-request updates it in place.
    CONSTRAINT unique_access_request UNIQUE (tournament_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_access_tournament ON public.tournament_access(tournament_id, status);
CREATE INDEX IF NOT EXISTS idx_access_user ON public.tournament_access(user_id, status);

ALTER TABLE public.tournament_access ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS select_own_access ON public.tournament_access;
CREATE POLICY select_own_access ON public.tournament_access FOR SELECT
    USING (
        user_id = auth.uid()
        OR EXISTS (
            SELECT 1 FROM public.tournaments t
            WHERE t.id = tournament_id AND t.owner_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS insert_own_access ON public.tournament_access;
CREATE POLICY insert_own_access ON public.tournament_access FOR INSERT
    WITH CHECK (user_id = auth.uid());

-- Only the owner decides; the service role bypasses RLS for the API itself.
DROP POLICY IF EXISTS owner_manages_access ON public.tournament_access;
CREATE POLICY owner_manages_access ON public.tournament_access FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM public.tournaments t
            WHERE t.id = tournament_id AND t.owner_id = auth.uid()
        )
    );

-- -----------------------------------------------------------------------------
-- 3. Notification type for access decisions
-- -----------------------------------------------------------------------------
ALTER TABLE public.notifications DROP CONSTRAINT IF EXISTS notifications_type_check;
ALTER TABLE public.notifications ADD CONSTRAINT notifications_type_check
    CHECK (type IN (
        'tournament_published',
        'registration_confirmed',
        'registration_closed',
        'schedule_published',
        'match_approaching',
        'result_confirmed',
        'knockout_advanced',
        'access_requested',
        'access_granted',
        'access_denied',
        'access_revoked'
    ));

-- -----------------------------------------------------------------------------
-- 4. Walkovers
--
-- Someone not turning up is normal at a real event; without this the only way
-- to resolve the match was to edit rows by hand.
-- -----------------------------------------------------------------------------
ALTER TABLE public.matches
    ADD COLUMN IF NOT EXISTS walkover BOOLEAN DEFAULT false NOT NULL;
ALTER TABLE public.matches
    ADD COLUMN IF NOT EXISTS walkover_reason TEXT;

-- -----------------------------------------------------------------------------
-- 5. Public directory view — name and club only
--
-- GET /api/players is unauthenticated and was returning whole profile rows,
-- publishing every participant's phone number and email address.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW public.player_directory AS
    SELECT id, name, avatar, club, city, rating, role, created_at
    FROM public.profiles
    WHERE role = 'player';

GRANT SELECT ON public.player_directory TO anon, authenticated;

DO $$
BEGIN
    RAISE NOTICE 'Migration 003 applied: ownership, delegated access, walkovers, PII-safe directory.';
END $$;
