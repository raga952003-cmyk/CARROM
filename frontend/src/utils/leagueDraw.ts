import { Match, TournamentRules } from '../types/tournament';

/** A flat, points-based league can settle equal totals as a draw.
 * Best-of-games and knockout matches still require a winner.
 */
export function canConfirmLeagueDraw(match: Match, rules: Partial<TournamentRules>): boolean {
  return match.stage === 'league'
    && rules.scoringMode === 'remaining_coins'
    && (match.numberOfSets || rules.numberOfSets || 1) === 1
    && rules.setWinnerRule !== 'target_points'
    && (rules.tieBreak !== 'most_board_wins' || match.player1BoardWins === match.player2BoardWins)
    && match.status !== 'cancelled'
    && !match.winnerId && !match.resultConfirmed && !match.walkover
    && match.player1TotalPoints === match.player2TotalPoints
    && !!match.boards?.length
    && match.boards.every(board => board.status === 'completed');
}
