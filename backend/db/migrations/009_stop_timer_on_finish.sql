-- =============================================================================
-- 009 — Stop the match clock when the match ends
--
-- A match keeps its elapsed time in two halves: timer_elapsed_seconds, which is
-- only added to on pause, and timer_started_at, the epoch milliseconds of the
-- current run. The UI stops counting when is_timer_running goes false.
--
-- Nothing set that flag false when a match finished. Start, pause and resume
-- each maintained the timer, but the paths that END a match -- the last board
-- completing it, and the umpire confirming the result -- wrote
-- status = 'completed' and nothing else. The clock went on running on a match
-- that had already been won, and the stored duration stayed at whatever it was
-- at the last pause instead of the time the match actually took.
--
-- Four code paths reach 'completed': two in the application, and two inside
-- SECURITY DEFINER functions whose bodies differ depending on which of
-- migrations 002, 005 and 007 have been applied. Rather than rewrite three
-- variants of two functions, a trigger closes the clock whenever a match
-- arrives at 'completed' by any route at all -- including a hand correction in
-- the SQL editor.
--
-- The application stops the clock too, so a database without this migration
-- still behaves. The two do not double-count: the trigger only acts when the
-- flag is still set, and the application clears it in the same write.
--
-- Safe to re-run.
-- =============================================================================

DO $$
BEGIN
    IF to_regclass('public.matches') IS NULL THEN
        RAISE EXCEPTION
            'Base schema missing in this database (current_database=%). '
            'Run db/schema.sql first, or switch to the project whose ref '
            'matches SUPABASE_URL in backend/.env.', current_database();
    END IF;
END $$;

CREATE OR REPLACE FUNCTION public.stop_timer_on_match_complete()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.status = 'completed' AND NEW.is_timer_running THEN
        -- Bank the stretch this run has been going before clearing the flag,
        -- or the recorded duration loses the final part of the match.
        NEW.timer_elapsed_seconds :=
            CASE
                WHEN NEW.timer_started_at IS NOT NULL THEN
                    COALESCE(NEW.timer_elapsed_seconds, 0)
                    + GREATEST(
                        0,
                        (((EXTRACT(EPOCH FROM now()) * 1000)::BIGINT - NEW.timer_started_at) / 1000)::INTEGER
                      )
                ELSE COALESCE(NEW.timer_elapsed_seconds, 0)
            END;
        NEW.is_timer_running := false;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_stop_timer_on_match_complete ON public.matches;
CREATE TRIGGER trg_stop_timer_on_match_complete
    BEFORE UPDATE ON public.matches
    FOR EACH ROW
    EXECUTE FUNCTION public.stop_timer_on_match_complete();

-- Matches already finished with the clock left running. How long they really
-- took is not recoverable, so only the flag is corrected -- which is the part
-- that stops the display counting.
UPDATE public.matches
SET is_timer_running = false
WHERE status = 'completed' AND is_timer_running = true;

DO $$
BEGIN
    RAISE NOTICE 'Migration 009 applied: the match clock now stops when a match completes.';
END $$;
