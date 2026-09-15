/**
 * Which matches are being played, which are still to come, and which are over.
 *
 * One helper because two screens ask the question and they must not answer it
 * differently. The public board and the player's dashboard were each deciding
 * for themselves, and the public board got it wrong in two ways worth naming,
 * since both are easy to reintroduce:
 *
 *   - It treated anything not `live` as upcoming. A PAUSED match is in play --
 *     the umpire stopped the clock for a dispute or a break -- so it appeared
 *     under "Upcoming" while two people were sitting at the board.
 *   - It treated only `resultConfirmed` as finished. A match the umpire has
 *     finished scoring but not yet confirmed also fell through to "Upcoming",
 *     so a completed match advertised itself as still to come.
 *
 * The rule here is that the STATUS decides, because the status is what the
 * match itself says it is. `resultConfirmed` is a separate fact -- whether the
 * organiser has signed it off -- and `finishedIsProvisional` exposes it so a
 * screen can mark a result as not-yet-official without moving it out of the
 * finished list.
 */
import { Match } from '../types/tournament';
import { compareMatches } from './matchOrder';

export interface MatchGroups {
  /** Being played right now, paused ones included. Earliest first. */
  live: Match[];
  /** Still to come. Earliest first, so the top of the list is next up. */
  upcoming: Match[];
  /** Over. MOST RECENT FIRST -- the opposite of the other two. */
  finished: Match[];
}

export type MatchGroupKey = keyof MatchGroups;

/** In play, clock running or not. */
export function isLive(match: Match): boolean {
  return match.status === 'live' || match.status === 'paused';
}

/**
 * Over.
 *
 * A walkover counts: nobody plays it, but it has a winner and it is not
 * something anyone is waiting for. Without this a walkover sits in "Upcoming"
 * for the rest of the tournament.
 */
export function isFinished(match: Match): boolean {
  return match.status === 'completed' || !!match.resultConfirmed || !!match.walkover;
}

/** A finished match the organiser has not signed off yet. */
export function finishedIsProvisional(match: Match): boolean {
  return isFinished(match) && !match.resultConfirmed;
}

/**
 * Split matches three ways.
 *
 * Every match lands in exactly one group: `live` is checked first so a paused
 * match cannot also read as upcoming, and the three predicates below are
 * exhaustive, so nothing is silently dropped from all three lists.
 *
 * Finished matches come back newest first. A player scrolling their results
 * wants the one they just played, not the one from Saturday morning, and
 * `matchCompletedAt` is preferred over the schedule because a match rarely
 * finishes in the order it was scheduled.
 */
export function groupMatches(matches: Match[] | undefined | null): MatchGroups {
  const all = matches || [];

  const live: Match[] = [];
  const upcoming: Match[] = [];
  const finished: Match[] = [];

  for (const match of all) {
    if (isLive(match)) live.push(match);
    else if (isFinished(match)) finished.push(match);
    else upcoming.push(match);
  }

  live.sort(compareMatches);
  upcoming.sort(compareMatches);
  finished.sort(compareFinished);

  return { live, upcoming, finished };
}

/**
 * Most recently finished first.
 *
 * `matchCompletedAt` is an ISO timestamp when it is set at all; it is absent on
 * walkovers and on matches recorded before that column existed, and those fall
 * back to the schedule. Missing timestamps sort LAST within the finished list
 * rather than first -- an unknown finish time is not "just now".
 */
export function compareFinished(a: Match, b: Match): number {
  const endA = a.matchCompletedAt || '';
  const endB = b.matchCompletedAt || '';

  if (endA !== endB) {
    if (!endA) return 1;
    if (!endB) return -1;
    return endB.localeCompare(endA);   // reversed: newest first
  }

  // Neither carries a finish time, or both carry the same one: fall back to
  // the schedule, also reversed.
  return compareMatches(b, a);
}

/**
 * How a finished match ended, as a line of text.
 *
 * Returns null when the match is not finished or has no recorded winner, so a
 * caller can render nothing rather than "undefined won".
 *
 * `withScore` exists because of a genuine ambiguity when this line sits under
 * a scoreboard. The scoreboard quotes player1–player2, in the order the two
 * names are listed; this sentence quotes the WINNER first, which is how a
 * result is normally read aloud. Both are right, and next to each other they
 * look like a contradiction:
 *
 *     Srinivasan / Sethupathi          1–5
 *     Sethupathi won 5–1 on boards
 *
 * So a caller already showing the score passes `withScore: false` and gets
 * "Sethupathi won", with the digits left to the scoreboard above.
 */
export function resultSummary(
  match: Match,
  options: { withScore?: boolean } = {},
): string | null {
  const { withScore = true } = options;

  if (!isFinished(match) || !match.winnerName) return null;

  if (match.walkover) {
    return `${match.winnerName} won by walkover`;
  }
  if (!withScore) {
    return `${match.winnerName} won`;
  }

  // Sets, when the format uses them, otherwise boards.
  const usesSets = (match.numberOfSets || 1) > 1;
  const a = usesSets ? match.player1SetsWon : match.player1BoardWins;
  const b = usesSets ? match.player2SetsWon : match.player2BoardWins;

  if (a === undefined || b === undefined) return `${match.winnerName} won`;

  // Winner's figure first, so the score reads in the same direction as the
  // sentence rather than needing to be matched back to the names.
  const winnerIsPlayer1 = match.winnerId
    ? match.winnerId === match.player1Id
    : match.winnerName === match.player1Name;
  const high = winnerIsPlayer1 ? a : b;
  const low = winnerIsPlayer1 ? b : a;

  return `${match.winnerName} won ${high}–${low} ${usesSets ? 'in sets' : 'on boards'}`;
}

/** Did this viewer win? null when the match is unfinished or not theirs. */
export function outcomeFor(
  match: Match,
  user: { id?: string; name?: string } | null | undefined,
): 'won' | 'lost' | null {
  if (!isFinished(match) || !match.winnerName || !user) return null;

  const name = (user.name || '').trim().toLowerCase();
  const isMine =
    (user.id && (match.player1Id === user.id || match.player2Id === user.id)) ||
    (!!name && ((match.player1Name || '').trim().toLowerCase() === name ||
                (match.player2Name || '').trim().toLowerCase() === name));
  if (!isMine) return null;

  const wonById = !!user.id && match.winnerId === user.id;
  const wonByName = !!name && (match.winnerName || '').trim().toLowerCase() === name;
  return wonById || wonByName ? 'won' : 'lost';
}
