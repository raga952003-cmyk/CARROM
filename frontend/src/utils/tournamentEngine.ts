import { 
  BoardScore, 
  Match, 
  Player, 
  Registration, 
  StandingsRow, 
  Team, 
  Tournament, 
  TournamentRules 
} from '../types/tournament';

// Helper to format dates & times
export function formatTimeSlot(baseDate: string, minutesFromStart: number): { date: string; time: string } {
  const d = new Date(baseDate);
  if (isNaN(d.getTime())) {
    return { date: baseDate, time: "10:00 AM" };
  }
  
  // Set start hour to 09:00 AM if not specified
  d.setHours(9, 0, 0, 0);
  d.setMinutes(d.getMinutes() + minutesFromStart);

  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  const formattedDate = `${year}-${month}-${day}`;

  let hours = d.getHours();
  const mins = String(d.getMinutes()).padStart(2, '0');
  const ampm = hours >= 12 ? 'PM' : 'AM';
  hours = hours % 12;
  hours = hours ? hours : 12; // 0 should be 12
  const formattedTime = `${String(hours).padStart(2, '0')}:${mins} ${ampm}`;

  return { date: formattedDate, time: formattedTime };
}

/**
 * Generates initial empty boards for a match based on tournament rules
 */
export function createEmptyBoards(maxBoards: number = 3): BoardScore[] {
  const boards: BoardScore[] = [];
  for (let i = 1; i <= maxBoards; i++) {
    boards.push({
      boardNumber: i,
      status: i === 1 ? 'in_progress' : 'pending',
      player1Score: 0,
      player2Score: 0,
      queenClaimedBy: 'none',
      queenCovered: false,
      foulsPlayer1: 0,
      foulsPlayer2: 0,
      whiteCoinsPocketed: 0,
      blackCoinsPocketed: 0
    });
  }
  return boards;
}

/**
 * Generate Round Robin / League fixtures
 */
export function generateRoundRobinFixtures(
  tournamentId: string,
  participants: (Player | Team)[],
  maxBoards: number = 3
): Match[] {
  const n = participants.length;
  if (n < 2) return [];

  // If odd number of participants, add a dummy bye
  const pool = [...participants];
  const hasBye = n % 2 !== 0;
  if (hasBye) {
    pool.push({ id: '__BYE__', name: 'BYE' } as any);
  }

  const numParticipants = pool.length;
  const numRounds = numParticipants - 1;
  const matchesPerRound = numParticipants / 2;
  const matches: Match[] = [];
  let matchCounter = 1;

  // Circle / Polygon algorithm for round robin
  for (let round = 0; round < numRounds; round++) {
    for (let matchIdx = 0; matchIdx < matchesPerRound; matchIdx++) {
      const homeIdx = (round + matchIdx) % (numParticipants - 1);
      let awayIdx = (numParticipants - 1 - matchIdx + round) % (numParticipants - 1);
      
      // Last position remains fixed
      if (matchIdx === 0) {
        awayIdx = numParticipants - 1;
      }

      const p1 = pool[homeIdx];
      const p2 = pool[awayIdx];

      // Skip match if one participant is BYE
      if (p1.id === '__BYE__' || p2.id === '__BYE__') {
        continue;
      }

      const isDoubles = 'player1' in p1;

      matches.push({
        id: `m_${tournamentId}_rr_${round + 1}_${matchCounter}`,
        tournamentId,
        matchNumber: matchCounter++,
        roundName: `League Round ${round + 1}`,
        roundIndex: round + 1,
        stage: 'league',
        type: isDoubles ? 'doubles' : 'singles',
        player1Id: p1.id,
        player2Id: p2.id,
        player1Name: p1.name,
        player2Name: p2.name,
        player1Details: p1,
        player2Details: p2,
        boardNumber: 1, // To be assigned by schedule generator
        scheduledDate: '',
        scheduledTime: '',
        status: 'scheduled',
        timerElapsedSeconds: 0,
        isTimerRunning: false,
        boards: createEmptyBoards(maxBoards),
        maxBoards,
        resultConfirmed: false,
        player1BoardWins: 0,
        player2BoardWins: 0,
        player1TotalPoints: 0,
        player2TotalPoints: 0,
        auditHistory: []
      });
    }
  }

  return matches;
}

/**
 * Generate Single-Elimination Knockout bracket with proper round linking
 */
export function generateKnockoutBracket(
  tournamentId: string,
  participants: (Player | Team)[],
  maxBoards: number = 3
): Match[] {
  const count = participants.length;
  if (count < 2) return [];

  // Determine bracket size (power of 2: 2, 4, 8, 16, 32)
  let bracketSize = 2;
  while (bracketSize < count) {
    bracketSize *= 2;
  }

  const totalRounds = Math.log2(bracketSize);
  const matches: Match[] = [];
  let matchNumber = 1;

  // Mapping from round and match index to Match object
  const roundMatchesMap: Map<string, Match> = new Map();

  const getRoundTitle = (roundIdx: number, totalRounds: number): string => {
    const roundsFromFinal = totalRounds - roundIdx;
    if (roundsFromFinal === 0) return 'Final';
    if (roundsFromFinal === 1) return 'Semi Final';
    if (roundsFromFinal === 2) return 'Quarter Final';
    if (roundsFromFinal === 3) return 'Round of 16';
    return `Round ${roundIdx}`;
  };

  // Build structure from Final down to Round 1
  for (let r = 1; r <= totalRounds; r++) {
    const matchesInRound = Math.pow(2, totalRounds - r);
    const roundName = getRoundTitle(r, totalRounds);

    for (let m = 0; m < matchesInRound; m++) {
      const matchId = `m_${tournamentId}_ko_r${r}_m${m + 1}`;
      
      let p1Id = 'TBD';
      let p2Id = 'TBD';
      let p1Name = 'Winner TBD';
      let p2Name = 'Winner TBD';
      let p1Details: any = undefined;
      let p2Details: any = undefined;

      // In Round 1, assign seeded/registered players
      if (r === 1) {
        const idx1 = m * 2;
        const idx2 = m * 2 + 1;
        
        if (idx1 < participants.length) {
          const p1 = participants[idx1];
          p1Id = p1.id;
          p1Name = p1.name;
          p1Details = p1;
        }
        if (idx2 < participants.length) {
          const p2 = participants[idx2];
          p2Id = p2.id;
          p2Name = p2.name;
          p2Details = p2;
        }
      }

      const isDoubles = participants.length > 0 && 'player1' in participants[0];

      const matchObj: Match = {
        id: matchId,
        tournamentId,
        matchNumber: matchNumber++,
        roundName,
        roundIndex: r,
        stage: 'knockout',
        type: isDoubles ? 'doubles' : 'singles',
        player1Id: p1Id,
        player2Id: p2Id,
        player1Name: p1Name,
        player2Name: p2Name,
        player1Details: p1Details,
        player2Details: p2Details,
        boardNumber: 1,
        scheduledDate: '',
        scheduledTime: '',
        status: 'scheduled',
        timerElapsedSeconds: 0,
        isTimerRunning: false,
        boards: createEmptyBoards(maxBoards),
        maxBoards,
        resultConfirmed: false,
        player1BoardWins: 0,
        player2BoardWins: 0,
        player1TotalPoints: 0,
        player2TotalPoints: 0,
        bracketPosition: {
          round: r,
          matchIndex: m
        },
        auditHistory: []
      };

      roundMatchesMap.set(`${r}_${m}`, matchObj);
      matches.push(matchObj);
    }
  }

  // Link child matches to their next parent matches
  for (let r = 1; r < totalRounds; r++) {
    const matchesInRound = Math.pow(2, totalRounds - r);
    for (let m = 0; m < matchesInRound; m++) {
      const currentMatch = roundMatchesMap.get(`${r}_${m}`);
      const parentMatchIndex = Math.floor(m / 2);
      const parentMatch = roundMatchesMap.get(`${r + 1}_${parentMatchIndex}`);
      
      if (currentMatch && parentMatch) {
        currentMatch.nextMatchId = parentMatch.id;
        currentMatch.nextMatchSlot = m % 2 === 0 ? 'player1' : 'player2';
      }
    }
  }

  return matches;
}

/**
 * Generate League + Knockout (Hybrid format)
 */
export function generateLeagueKnockoutFixtures(
  tournamentId: string,
  participants: (Player | Team)[],
  maxBoards: number = 3
): Match[] {
  // Generate league matches
  const leagueMatches = generateRoundRobinFixtures(tournamentId, participants, maxBoards);
  
  // Also create placeholder semi-finals & finals knockout tree
  const topCount = Math.min(4, Math.max(2, Math.floor(participants.length / 2)));
  const dummyKnockoutParticipants: Player[] = [];
  for (let i = 1; i <= topCount; i++) {
    dummyKnockoutParticipants.push({
      id: `qualifier_${i}`,
      name: `League Rank #${i}`
    });
  }

  const knockoutMatches = generateKnockoutBracket(tournamentId, dummyKnockoutParticipants, maxBoards);
  
  // Adjust match numbering
  let counter = 1;
  leagueMatches.forEach(m => { m.matchNumber = counter++; });
  knockoutMatches.forEach(m => { m.matchNumber = counter++; });

  return [...leagueMatches, ...knockoutMatches];
}

/**
 * Intelligent Conflict-Free Schedule Generator
 * Constraints enforced:
 * 1. No participant scheduled for 2 matches simultaneously.
 * 2. No board assigned to 2 matches simultaneously.
 * 3. Match duration + rest buffer is respected.
 * 4. Spreads across available boards smoothly.
 */
export function generateConflictFreeSchedule(
  matches: Match[],
  numberOfBoards: number,
  startDate: string,
  matchDurationMinutes: number = 30,
  restTimeMinutes: number = 10
): Match[] {
  if (!matches || matches.length === 0) return [];

  const boardCount = Math.max(1, numberOfBoards);
  const slotDuration = matchDurationMinutes + restTimeMinutes;
  
  // Track participant availability: participantId -> next available minute timestamp
  const participantNextAvailable: Map<string, number> = new Map();
  // Track board availability: boardIndex (1..N) -> next available minute timestamp
  const boardNextAvailable: number[] = new Array(boardCount + 1).fill(0);

  const updatedMatches = matches.map(m => ({ ...m }));

  // Separate league / early rounds first, knockout rounds after
  const sortedMatches = [...updatedMatches].sort((a, b) => {
    if (a.stage !== b.stage) {
      return a.stage === 'league' ? -1 : 1;
    }
    return (a.roundIndex || 0) - (b.roundIndex || 0);
  });

  let currentSlotOffset = 0; // minutes from start

  for (const match of sortedMatches) {
    const p1 = match.player1Id;
    const p2 = match.player2Id;

    // Find earliest minute where:
    // 1) At least one board is free
    // 2) Both players are free (if known players)
    let earliestTime = currentSlotOffset;

    const p1Available = (p1 && p1 !== 'TBD') ? (participantNextAvailable.get(p1) || 0) : 0;
    const p2Available = (p2 && p2 !== 'TBD') ? (participantNextAvailable.get(p2) || 0) : 0;

    let matchEarliest = Math.max(earliestTime, p1Available, p2Available);

    // Find a board that is free at or before matchEarliest, or find the board that frees up earliest
    let chosenBoard = 1;
    let minBoardFreeTime = Infinity;

    for (let b = 1; b <= boardCount; b++) {
      if (boardNextAvailable[b] <= matchEarliest) {
        chosenBoard = b;
        minBoardFreeTime = boardNextAvailable[b];
        break;
      }
      if (boardNextAvailable[b] < minBoardFreeTime) {
        minBoardFreeTime = boardNextAvailable[b];
        chosenBoard = b;
      }
    }

    // Actual start time for this match
    const actualStartTime = Math.max(matchEarliest, minBoardFreeTime);
    const finishTime = actualStartTime + matchDurationMinutes;
    const nextAvailableTimeForPlayers = finishTime + restTimeMinutes;
    const nextAvailableTimeForBoard = finishTime + 5; // 5 min board prep

    // Record assignments
    match.boardNumber = chosenBoard;
    const { date, time } = formatTimeSlot(startDate, actualStartTime);
    match.scheduledDate = date;
    match.scheduledTime = time;

    // Update trackers
    boardNextAvailable[chosenBoard] = nextAvailableTimeForBoard;
    if (p1 && p1 !== 'TBD') participantNextAvailable.set(p1, nextAvailableTimeForPlayers);
    if (p2 && p2 !== 'TBD') participantNextAvailable.set(p2, nextAvailableTimeForPlayers);
  }

  return sortedMatches;
}

/**
 * Calculates official Points Table Standings from matches and participants
 */
export function calculatePointsTable(
  matches: Match[],
  participants: (Player | Team)[],
  rules: TournamentRules
): StandingsRow[] {
  const standingsMap: Map<string, StandingsRow> = new Map();

  // Initialize standings row for each participant
  for (const p of participants) {
    standingsMap.set(p.id, {
      rank: 1,
      participantId: p.id,
      participantName: p.name,
      played: 0,
      won: 0,
      lost: 0,
      drawn: 0,
      boardWins: 0,
      boardLosses: 0,
      boardDiff: 0,
      scoreFor: 0,
      scoreAgainst: 0,
      scoreDiff: 0,
      points: 0,
      form: [],
      participantType: 'player1' in p ? 'doubles' : 'singles'
    });
  }

  // Process all completed & confirmed matches (only stage === 'league' for points table)
  const leagueMatches = matches.filter(m => m.resultConfirmed && m.stage === 'league');

  for (const m of leagueMatches) {
    const s1 = standingsMap.get(m.player1Id);
    const s2 = standingsMap.get(m.player2Id);

    if (!s1 || !s2) continue;

    s1.played += 1;
    s2.played += 1;

    s1.boardWins += m.player1BoardWins;
    s1.boardLosses += m.player2BoardWins;
    s2.boardWins += m.player2BoardWins;
    s2.boardLosses += m.player1BoardWins;

    s1.scoreFor += m.player1TotalPoints;
    s1.scoreAgainst += m.player2TotalPoints;
    s2.scoreFor += m.player2TotalPoints;
    s2.scoreAgainst += m.player1TotalPoints;

    if (m.winnerId === s1.participantId) {
      s1.won += 1;
      s1.points += rules.pointsForWin;
      s1.form.push('W');

      s2.lost += 1;
      s2.points += rules.pointsForLoss;
      s2.form.push('L');
    } else if (m.winnerId === s2.participantId) {
      s2.won += 1;
      s2.points += rules.pointsForWin;
      s2.form.push('W');

      s1.lost += 1;
      s1.points += rules.pointsForLoss;
      s1.form.push('L');
    } else {
      // Draw
      s1.drawn += 1;
      s1.points += rules.pointsForDraw;
      s1.form.push('D');

      s2.drawn += 1;
      s2.points += rules.pointsForDraw;
      s2.form.push('D');
    }
  }

  // Calculate differentials and convert to array
  const rows: StandingsRow[] = Array.from(standingsMap.values()).map(r => ({
    ...r,
    boardDiff: r.boardWins - r.boardLosses,
    scoreDiff: r.scoreFor - r.scoreAgainst,
    form: r.form.slice(-5) // Last 5 results
  }));

  // Deterministic sorting with tiebreakers:
  // 1. Points (desc)
  // 2. Board Difference (desc)
  // 3. Score Difference (desc)
  // 4. Score For (desc)
  // 5. Name (asc)
  rows.sort((a, b) => {
    if (b.points !== a.points) return b.points - a.points;
    if (b.boardDiff !== a.boardDiff) return b.boardDiff - a.boardDiff;
    if (b.scoreDiff !== a.scoreDiff) return b.scoreDiff - a.scoreDiff;
    if (b.scoreFor !== a.scoreFor) return b.scoreFor - a.scoreFor;
    return a.participantName.localeCompare(b.participantName);
  });

  // Assign ranks
  rows.forEach((row, idx) => {
    row.rank = idx + 1;
    row.isQualified = idx < 4; // Top 4 advance
  });

  return rows;
}

/**
 * Determine the winner of a single board based on scores
 */
export function calculateBoardWinner(board: BoardScore): 'player1' | 'player2' | 'draw' | 'in_progress' {
  if (board.status !== 'completed') return 'in_progress';
  if (board.player1Score > board.player2Score) return 'player1';
  if (board.player2Score > board.player1Score) return 'player2';
  return 'draw';
}

/**
 * Recalculate match statistics from board scores
 */
export function recalculateMatchScores(match: Match): Match {
  let p1BoardWins = 0;
  let p2BoardWins = 0;
  let p1Total = 0;
  let p2Total = 0;

  for (const b of match.boards) {
    if (b.status === 'completed') {
      p1Total += b.player1Score;
      p2Total += b.player2Score;
      if (b.player1Score > b.player2Score) {
        p1BoardWins += 1;
      } else if (b.player2Score > b.player1Score) {
        p2BoardWins += 1;
      }
    }
  }

  let winnerId: string | undefined = undefined;
  let winnerName: string | undefined = undefined;

  // Winner is determined by board wins
  if (p1BoardWins > p2BoardWins) {
    winnerId = match.player1Id;
    winnerName = match.player1Name;
  } else if (p2BoardWins > p1BoardWins) {
    winnerId = match.player2Id;
    winnerName = match.player2Name;
  }

  return {
    ...match,
    player1BoardWins: p1BoardWins,
    player2BoardWins: p2BoardWins,
    player1TotalPoints: p1Total,
    player2TotalPoints: p2Total,
    winnerId,
    winnerName
  };
}

/**
 * Advance winner in the Knockout Bracket to the next match
 */
export function advanceWinnerInBracket(
  matches: Match[],
  completedMatch: Match
): Match[] {
  if (!completedMatch.nextMatchId || !completedMatch.winnerId) {
    return matches;
  }

  return matches.map(m => {
    if (m.id === completedMatch.nextMatchId) {
      const isPlayer1Slot = completedMatch.nextMatchSlot === 'player1';
      return {
        ...m,
        player1Id: isPlayer1Slot ? (completedMatch.winnerId || 'TBD') : m.player1Id,
        player1Name: isPlayer1Slot ? (completedMatch.winnerName || 'Winner TBD') : m.player1Name,
        player1Details: isPlayer1Slot ? completedMatch.player1Details : m.player1Details,
        player2Id: !isPlayer1Slot ? (completedMatch.winnerId || 'TBD') : m.player2Id,
        player2Name: !isPlayer1Slot ? (completedMatch.winnerName || 'Winner TBD') : m.player2Name,
        player2Details: !isPlayer1Slot ? completedMatch.player2Details : m.player2Details
      };
    }
    return m;
  });
}
