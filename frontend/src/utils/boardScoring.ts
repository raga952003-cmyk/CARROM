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
  finishType?: 'normal' | 'own_last_coin_queen_left';
  specialFinishExtraPoint?: boolean;
  queenPocketedBy: Side;
  queenCoveredBy: Side;
  coinsRemainingWith: Side;
  coinsRemaining: number;
  p1Penalty: number;
  p2Penalty: number;
}

export const emptyObservation: BoardObservation = {
  winner: 'none',
  finishType: 'normal',
  specialFinishExtraPoint: false,
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
export interface SideNames {
  player1: string;
  player2: string;
}

export interface PriorGamePoints {
  player1: number;
  player2: number;
}

const SIDE_LABELS: SideNames = { player1: 'Player 1', player2: 'Player 2' };

export function previewBoard(
  obs: BoardObservation,
  rules: Partial<TournamentRules>,
  names: SideNames = SIDE_LABELS,
  priorGamePoints: PriorGamePoints = { player1: 0, player2: 0 },
) {
  // A warning is read by the umpire mid-match, so it names the player rather
  // than the field the value happens to be stored in.
  const who = (side: Side) => (side === 'none' ? 'nobody' : names[side]);
  const queenPoints = rules.queenPoints ?? 3;
  const coinValue = rules.coinValue ?? 1;
  const coinsPerSide = rules.coinsPerSide ?? 9;
  const mustCover = rules.queenMustBeCovered !== false;
  const awardTo = rules.queenAwardTo ?? 'coverer';
  const warnings: string[] = [];

  if (rules.scoringMode === 'official_icf') {
    const winner = obs.winner;
    if (obs.finishType === 'own_last_coin_queen_left') {
      const warnings: string[] = [];
      if (winner === 'none') warnings.push('Choose who pocketed their last coin while the queen remained on the board.');
      const base = winner === 'none' ? 0 : (priorGamePoints[winner] >= 22 ? 1 : 3);
      const extra = obs.specialFinishExtraPoint ? 1 : 0;
      const points = winner === 'none' ? 0 : base + extra;
      return {
        p1: winner === 'player1' ? points : 0,
        p2: winner === 'player2' ? points : 0,
        base: points,
        queenBonus: 0,
        queenSide: 'none' as Side,
        queenStatus: 'not_pocketed' as const,
        warnings,
      };
    }
    const loser = winner === 'player1' ? 'player2' : winner === 'player2' ? 'player1' : 'none';
    const validCoins = Number.isInteger(obs.coinsRemaining)
      && obs.coinsRemaining >= 0 && obs.coinsRemaining <= 9;
    if (winner === 'none') warnings.push('Choose the player who won this board.');
    if (loser !== 'none' && obs.coinsRemainingWith !== loser) {
      warnings.push(`The coins left on the board must belong to ${who(loser)}.`);
    }
    if (!validCoins) warnings.push('Enter 0 to 9 opposing coins left on the board.');
    if (obs.queenPocketedBy !== 'none' && obs.queenCoveredBy !== 'none'
        && obs.queenPocketedBy !== obs.queenCoveredBy) {
      warnings.push('The queen must be covered by the player who pocketed it.');
    }
    if (obs.queenPocketedBy === 'none' && obs.queenCoveredBy !== 'none') {
      warnings.push('A queen cannot be covered without being pocketed.');
    }
    const covered = obs.queenPocketedBy !== 'none'
      && obs.queenCoveredBy === obs.queenPocketedBy;
    const queenStatus: 'not_pocketed' | 'covered' | 'returned' =
      obs.queenPocketedBy === 'none' ? 'not_pocketed' : covered ? 'covered' : 'returned';
    const eligible = winner !== 'none' && (rules.targetScore === 21 || priorGamePoints[winner] <= 21);
    const queenBonus = covered && obs.queenPocketedBy === winner && eligible ? 3 : 0;
    const base = validCoins && obs.coinsRemainingWith === loser && winner !== 'none'
      ? obs.coinsRemaining : 0;
    const penalty = winner === 'player1' ? obs.p1Penalty : winner === 'player2' ? obs.p2Penalty : 0;
    const winnerPoints = Math.min(12, Math.max(0, base + queenBonus - Math.max(0, penalty)));
    return {
      p1: winner === 'player1' ? winnerPoints : 0,
      p2: winner === 'player2' ? winnerPoints : 0,
      base, queenBonus, queenSide: queenBonus ? winner : 'none' as Side,
      queenStatus, warnings,
    };
  }

  let base = 0;
  if (obs.winner !== 'none' && obs.coinsRemainingWith !== 'none') {
    if (obs.coinsRemainingWith === obs.winner) {
      warnings.push(`${who(obs.winner)} is marked as both the board winner and the side holding the coins left — no base points.`);
    } else {
      base = Math.max(0, obs.coinsRemaining);
      // Mirrors the same clamp in board_result(): a side cannot have more
      // coins left than it started with, and an unclamped count was scored
      // verbatim — a mistyped 19 became a 19-point board.
      if (base > coinsPerSide) {
        warnings.push(
          `${obs.coinsRemaining} coins remaining is more than the ${coinsPerSide} ` +
          `a side can hold — scored ${coinsPerSide}.`
        );
        base = coinsPerSide;
      }
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
        `${who(obs.queenPocketedBy)} pocketed the queen but ${who(obs.queenCoveredBy)} covered it — ` +
        `the ${queenPoints} points went to ${who(queenSide)}.`
      );
    }
  } else if (queenStatus === 'returned') {
    warnings.push('The queen was pocketed but not covered — it scores nothing and returns to the board.');
  }

  const pts: Record<'player1' | 'player2', number> = { player1: 0, player2: 0 };
  if (obs.winner !== 'none') pts[obs.winner] += base * coinValue;
  if (queenSide !== 'none') pts[queenSide] += queenBonus;
  pts.player1 = Math.max(0, pts.player1 - Math.max(0, obs.p1Penalty));
  pts.player2 = Math.max(0, pts.player2 - Math.max(0, obs.p2Penalty));

  return { p1: pts.player1, p2: pts.player2, base: base * coinValue,
           queenBonus, queenSide, queenStatus, warnings };
}
