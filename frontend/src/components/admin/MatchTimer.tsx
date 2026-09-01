import React, { useEffect, useState } from 'react';
import { Match } from '../../types/tournament';

/**
 * Live elapsed time for a match.
 *
 * The backend accumulates `timerElapsedSeconds` only when a match is paused;
 * while it runs, the true elapsed time is that total plus the time since
 * `timerStartedAt`. That sum was never rendered anywhere, so an umpire had no
 * way to see how long a match had been going.
 */
export function elapsedSeconds(
  match: Pick<Match, 'timerElapsedSeconds' | 'timerStartedAt' | 'isTimerRunning' | 'status'>,
): number {
  const base = match.timerElapsedSeconds || 0;
  // A finished match is not running, whatever the flag says. The flag was left
  // set on every match completed before this was fixed, and those rows would
  // otherwise go on counting for as long as the page is open.
  if (match.status === 'completed') return base;
  if (!match.isTimerRunning || !match.timerStartedAt) return base;

  const delta = Math.floor((Date.now() - match.timerStartedAt) / 1000);
  // A clock skew between the server and this browser must not turn into an
  // absurd reading; fall back to the stored total instead of showing hours.
  if (delta < 0 || delta > 24 * 60 * 60) return base;
  return base + delta;
}

export function formatElapsed(total: number): string {
  const s = Math.max(0, Math.floor(total));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`;
}

interface MatchTimerProps {
  match: Match;
  className?: string;
}

export const MatchTimer: React.FC<MatchTimerProps> = ({ match, className = '' }) => {
  const [now, setNow] = useState(() => elapsedSeconds(match));

  useEffect(() => {
    setNow(elapsedSeconds(match));
    if (!match.isTimerRunning || match.status === 'completed') return;
    const id = setInterval(() => setNow(elapsedSeconds(match)), 1000);
    return () => clearInterval(id);
  }, [match.isTimerRunning, match.timerStartedAt, match.timerElapsedSeconds, match.status]);

  return (
    <span className={className} aria-live="off" title="Elapsed match time">
      {formatElapsed(now)}
    </span>
  );
};
