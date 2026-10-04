import assert from 'node:assert/strict';
import { canConfirmLeagueDraw } from '../../src/utils/leagueDraw';
import { Match } from '../../src/types/tournament';

const match = { stage: 'league', status: 'live', numberOfSets: 1,
  player1TotalPoints: 20, player2TotalPoints: 20,
  player1BoardWins: 1, player2BoardWins: 1, tieBreakRequired: true,
  boards: [{ status: 'completed' }, { status: 'completed' }] } as Match;
const rules = { scoringMode: 'remaining_coins', setWinnerRule: 'total_points' } as const;
assert.equal(canConfirmLeagueDraw(match, rules), true, 'Completed simple league tie can be drawn');
assert.equal(canConfirmLeagueDraw({ ...match, stage: 'knockout' }, rules), false, 'Knockout needs a winner');
assert.equal(canConfirmLeagueDraw({ ...match, numberOfSets: 3 }, rules), false, 'Best of games needs a winner');
assert.equal(canConfirmLeagueDraw(match, { ...rules, setWinnerRule: 'target_points' }), false);
assert.equal(canConfirmLeagueDraw({ ...match, player2TotalPoints: 19 }, rules), false);
assert.equal(canConfirmLeagueDraw({ ...match, boards: [] }, rules), false, 'Unplayed match cannot be drawn');
assert.equal(canConfirmLeagueDraw({ ...match, boards: [{ status: 'pending' }] } as Match, rules), false);
assert.equal(canConfirmLeagueDraw({ ...match, resultConfirmed: true }, rules), false);
assert.equal(canConfirmLeagueDraw({ ...match, status: 'cancelled' }, rules), false);
assert.equal(canConfirmLeagueDraw({ ...match, player1BoardWins: 2 }, { ...rules, tieBreak: 'most_board_wins' }), false);
console.log('League draw checks passed: league eligibility, knockout/game protection and unfinished match guards.');
