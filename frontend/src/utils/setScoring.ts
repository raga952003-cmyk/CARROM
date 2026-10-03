import { Match, MatchSet, TournamentRules, BoardScore, SetTieBreakDecision } from '../types/tournament';

export interface SetSummary extends MatchSet {
  needsExtraBoard: boolean;
  needsSetTieBreak: boolean;
  tieBreakResult?: SetTieBreakDecision | null;
}

function boardWinner(board: BoardScore): 'player1' | 'player2' | null {
  if (board.boardWinner === 'player1' || board.boardWinner === 'player2') return board.boardWinner;
  if (board.player1Score > board.player2Score) return 'player1';
  if (board.player2Score > board.player1Score) return 'player2';
  return null;
}

/** The same game boundaries and per-game ruling check used by summarise_sets on the server. */
export function summariseMatchSets(match: Match, rules: Partial<TournamentRules> = {}): SetSummary[] {
  const totalSets = Math.max(1, match.numberOfSets || rules.numberOfSets || 1);
  const perSet = Math.max(1, rules.boardsPerSet || match.maxBoards || 8);
  const setRule = rules.setWinnerRule || 'total_points';
  const target = Math.max(1, rules.targetScore || match.targetPoints || 25);
  const official21Six = rules.scoringMode === 'official_icf'
    && setRule === 'target_points' && rules.targetScore === 21 && perSet === 6;

  return Array.from({ length: totalSets }, (_, index) => {
    const setNumber = index + 1;
    const members = (match.boards || [])
      .filter(board => (board.setNumber || 1) === setNumber)
      .sort((a, b) => a.boardNumber - b.boardNumber);
    let player1Points = 0;
    let player2Points = 0;
    let boardsCompleted = 0;
    let complete = false;

    for (const board of members) {
      if (board.status !== 'completed') continue;
      boardsCompleted++;
      player1Points += board.player1Score || 0;
      player2Points += board.player2Score || 0;
      if (setRule === 'target_points' && (
        (boardsCompleted <= perSet && Math.max(player1Points, player2Points) >= target
          && player1Points !== player2Points)
        || (boardsCompleted >= perSet && player1Points !== player2Points)
      )) {
        complete = true;
        break;
      }
    }

    const boardsExpected = Math.max(members.length, perSet);
    if (setRule !== 'target_points') complete = boardsCompleted > 0 && boardsCompleted >= boardsExpected;
    const tiedAtLimit = setRule === 'target_points' && !complete
      && boardsCompleted >= perSet && player1Points === player2Points
      && members.every(board => board.status === 'completed');
    let needsSetTieBreak = official21Six && tiedAtLimit && members.length === perSet;
    const storedDecision = match.setTieBreaks?.[String(setNumber)];
    const tieBreakResult = needsSetTieBreak && storedDecision?.method === 'sudden_death'
      && (storedDecision.winnerId === match.player1Id || storedDecision.winnerId === match.player2Id)
      ? storedDecision : null;
    if (tieBreakResult) {
      complete = true;
      needsSetTieBreak = false;
    }
    const needsExtraBoard = tiedAtLimit && !(official21Six && members.length === perSet);

    let winnerId: string | null = null;
    if (complete) {
      if (tieBreakResult) {
        winnerId = tieBreakResult.winnerId;
      } else if (setRule === 'board_wins') {
        const p1 = members.filter(board => board.status === 'completed' && boardWinner(board) === 'player1').length;
        const p2 = members.filter(board => board.status === 'completed' && boardWinner(board) === 'player2').length;
        winnerId = p1 > p2 ? match.player1Id : p2 > p1 ? match.player2Id : null;
      } else {
        winnerId = player1Points > player2Points ? match.player1Id
          : player2Points > player1Points ? match.player2Id : null;
      }
    }

    return {
      setNumber,
      status: complete ? 'completed' : boardsCompleted ? 'in_progress' : 'pending',
      boardsCompleted,
      boardsExpected,
      player1Points,
      player2Points,
      winnerId,
      winnerName: !winnerId ? null : winnerId === match.player1Id ? match.player1Name
        : winnerId === match.player2Id ? match.player2Name : null,
      needsExtraBoard,
      needsSetTieBreak,
      tieBreakResult,
    };
  });
}

/** Return the game the server is asking to settle, never a match-wide award. */
export function pendingOfficialSetTieBreak(
  match: Match | undefined,
  rules: Partial<TournamentRules> | undefined,
): SetSummary | null {
  if (!match || !match.tieBreakRequired || match.tieBreakRule !== 'sudden_death') return null;
  return summariseMatchSets(match, rules).find(set => set.needsSetTieBreak) || null;
}

export function setTieBreakPath(matchId: string, setNumber: number): string {
  return `/matches/${matchId}/sets/${setNumber}/tie-break`;
}
