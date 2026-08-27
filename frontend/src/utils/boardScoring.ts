import { Side, TournamentRules } from '../types/tournament';

/**
 * The board scoring rules, in the browser.
 *
 * This mirrors `board_result()` in backend/app/services/scoring_engine.py so the
 * umpire sees the score before saving it. The server recomputes it and remains
 * authoritative — this is a preview, never the stored value.
 *
 * It lives apart from the form because the one thing that must never happen is
 * the preview and the server disagreeing, and logic tangled up in a React
 * component cannot be tested against the Python it mirrors.
 */
export interface BoardObservation {
  winner: Side;
  queenPocketedBy: Side;
  queenCoveredBy: Side;
  coinsRemainingWith: Side;
  coinsRemaining: number;
  p1Penalty: number;
  p2Penalty: number;
}

export const emptyObservation: BoardObservation = {
  winner: 'none',
  queenPocketedBy: 'none',
  queenCoveredBy: 'none',
  coinsRemainingWith: 'none',
  coinsRemaining: 0,
  p1Penalty: 0,
  p2Penalty: 0,
};

/**
 * Mirrors the backend `board_result()` so the umpire sees the score before
 * saving. The server recomputes it and stays authoritative; this is a preview.
 */
export function previewBoard(obs: BoardObservation, rules: Partial<TournamentRules>) {
  const queenPoints = rules.queenPoints ?? 3;
  const mustCover = rules.queenMustBeCovered !== false;
  const awardTo = rules.queenAwardTo ?? 'coverer';
  const warnings: string[] = [];

  let base = 0;
  if (obs.winner !== 'none' && obs.coinsRemainingWith !== 'none') {
    if (obs.coinsRemainingWith === obs.winner) {
      warnings.push('The winner is also marked as holding the coins left on the board — no base points.');
    } else {
      base = Math.max(0, obs.coinsRemaining);
    }
  }

  const covered = obs.queenCoveredBy !== 'none';
  const queenStatus: 'not_pocketed' | 'covered' | 'returned' =
    obs.queenPocketedBy === 'none' ? 'not_pocketed' : (covered || !mustCover) ? 'covered' : 'returned';

  if (covered && obs.queenPocketedBy === 'none') {
    warnings.push('The queen is marked as covered but nobody is marked as pocketing it.');
  }

  let queenSide: Side = 'none';
  let queenBonus = 0;
  if (queenStatus === 'covered' && obs.queenPocketedBy !== 'none') {
    queenSide = awardTo === 'coverer' && covered ? obs.queenCoveredBy : obs.queenPocketedBy;
    queenBonus = queenPoints;
    if (covered && obs.queenCoveredBy !== obs.queenPocketedBy) {
      // Worth saying out loud: it explains a bonus landing on the side that
      // did not sink the queen, which otherwise reads as a mistake.
      warnings.push(
        `${obs.queenPocketedBy} pocketed the queen but ${obs.queenCoveredBy} covered it — ` +
        `the ${queenPoints} points went to ${queenSide}.`
      );
    }
  } else if (queenStatus === 'returned') {
    warnings.push('The queen was pocketed but not covered — it scores nothing and returns to the board.');
  }

  const pts: Record<'player1' | 'player2', number> = { player1: 0, player2: 0 };
  if (obs.winner !== 'none') pts[obs.winner] += base;
  if (queenSide !== 'none') pts[queenSide] += queenBonus;
  pts.player1 = Math.max(0, pts.player1 - Math.max(0, obs.p1Penalty));
  pts.player2 = Math.max(0, pts.player2 - Math.max(0, obs.p2Penalty));

  return { p1: pts.player1, p2: pts.player2, base, queenBonus, queenSide, queenStatus, warnings };
}
