-- =============================================================================
-- 009 — Stop the match clock when the match ends
--
-- A match keeps its elapsed time in two halves: timer_elapsed_seconds, which is
-- only added to on pause, and timer_started_at, the epoch milliseconds of the
-- current run. The UI stops counting when is_timer_running goes false.
--
-- Nothing set that flag false when a match finished. Start, pause and resume
-- each maintained the timer, but nothing closed it out, so the clock ran on for
-- as long as the page stayed open and the stored duration stayed at whatever it
-- was at the last pause.
--
-- What ends a match is the umpire confirming the result, NOT the last board
-- being scored. Between those two moments there is still work to do -- checking
-- the boards, settling a dispute, agreeing a tie-break -- and that time belongs
-- to the match. So the clock runs through 'completed' and stops on confirmation.
--
-- Confirmation happens in two places: the application fallback, and a
-- SECURITY DEFINER function whose body differs depending on which of migrations
-- 002, 005 and 007 have been applied. Rather than rewrite several variants of
-- it, a trigger closes the clock whenever result_confirmed becomes true by any
-- route at all -- including a hand correction in the SQL editor.
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
    IF NEW.result_confirmed AND NOT COALESCE(OLD.result_confirmed, false)
       AND NEW.is_timer_running THEN
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

-- Matches already confirmed with the clock left running. How long they really
-- took is not recoverable, so only the flag is corrected -- which is the part
-- that stops the display counting. A match that is merely 'completed' is left
-- alone: its clock is meant to still be running.
UPDATE public.matches
SET is_timer_running = false
WHERE result_confirmed = true AND is_timer_running = true;

DO $$
BEGIN
    RAISE NOTICE 'Migration 009 applied: the match clock now stops when a result is confirmed.';
END $$;
