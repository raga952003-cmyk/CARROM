export type TournamentFormat = 'round_robin' | 'knockout' | 'league_knockout';
export type MatchType = 'singles' | 'doubles';
/**
 * 'scheduled' and 'ongoing' are the original schema's names for
 * 'fixture_published' and 'in_progress'. Rows written before migration 002
 * still carry them, so both spellings stay in the union and every status
 * check has to accept either.
 */
export type TournamentStatus =
  | 'draft'
  | 'registration_open'
  | 'registration_closed'
  | 'fixture_generation'
  | 'fixture_published'
  | 'scheduled'
  | 'in_progress'
  | 'ongoing'
  | 'completed'
  | 'cancelled';
export type MatchStatus = 'scheduled' | 'live' | 'paused' | 'completed';
export type BoardStatus = 'pending' | 'in_progress' | 'completed';
export type UserRole = 'admin' | 'player';

export interface Admin {
  id: string;
  name: string;
  email: string;
  role: 'admin';
  created_at?: string;
}

export interface Player {
  id: string;
  name: string;
  avatar?: string;
  club?: string;
  city?: string;
  rating?: number;
  seed?: number;
  phone?: string;
  email?: string;
  password?: string;
  role?: 'player';
  created_at?: string;
}

export interface Team {
  id: string;
  name: string;
  player1: Player;
  player2: Player;
  club?: string;
  city?: string;
  rating?: number;
  seed?: number;
}

export interface Registration {
  id: string;
  tournamentId: string;
  type: MatchType;
  player?: Player;
  team?: Team;
  status: 'pending' | 'approved' | 'rejected';
  registeredAt: string;
  paymentStatus: 'paid' | 'waived' | 'pending';
  notes?: string;
}

export type Side = 'player1' | 'player2' | 'none';

export interface MatchSet {
  setNumber: number;
  status: 'pending' | 'in_progress' | 'completed';
  boardsCompleted: number;
  boardsExpected: number;
  player1Points: number;
  player2Points: number;
  winnerId?: string | null;
  winnerName?: string | null;
}

export interface BoardScore {
  /** Board numbers restart in each set, so a board is (set, number). */
  setNumber?: number;
  boardNumber: number;
  status: BoardStatus;
  /** The final points for the board, after queen and penalties. */
  player1Score: number;
  player2Score: number;

  // The umpire's observations. Each is recorded independently: who won the
  // board says nothing about who took the queen, and vice versa.
  boardWinner?: Side | null;
  p1CoinsPocketed?: number | null;
  p2CoinsPocketed?: number | null;
  coinsRemainingWith?: Side | null;
  coinsRemaining?: number | null;
  queenPocketedBy?: Side | null;
  queenCoveredBy?: Side | null;
  queenStatus?: 'not_pocketed' | 'covered' | 'returned' | null;
  queenAwardedTo?: Side | null;
  p1Penalty?: number | null;
  p2Penalty?: number | null;
  basePoints?: number | null;
  queenBonus?: number | null;
  scoringWarnings?: string[] | null;
  locked?: boolean;
  confirmedAt?: string | null;

  queenClaimedBy?: 'player1' | 'player2' | 'none';
  queenCovered?: boolean;
  foulsPlayer1?: number;
  foulsPlayer2?: number;
  whiteCoinsPocketed?: number;
  blackCoinsPocketed?: number;
  durationMinutes?: number;
  completedAt?: string;
  notes?: string;
}

export interface ScoreAuditLog {
  id: string;
  timestamp: string;
  adminName: string;
  boardNumber: number;
  previousScore: { player1: number; player2: number };
  newScore: { player1: number; player2: number };
  reason: string;
}

export interface Match {
  id: string;
  tournamentId: string;
  matchNumber: number;
  roundName: string; // e.g. "Round 1", "Quarter Final", "Semi Final", "Final"
  roundIndex: number;
  stage: 'league' | 'knockout';
  type: MatchType;
  player1Id: string; // Can be player ID or Team ID
  player2Id: string;
  player1Name: string;
  player2Name: string;
  player1Details?: Player | Team;
  player2Details?: Player | Team;
  boardNumber: number;
  scheduledDate: string;
  scheduledTime: string;
  status: MatchStatus;
  
  // Toss — recorded by the umpire before the first board
  tossCoinResult?: 'black' | 'white' | null;
  tossWinnerId?: string | null;
  tossWinnerName?: string | null;
  tossChoice?: 'strike' | 'side' | null;
  tossRecordedAt?: string | null;

  // Timer state
  timerStartedAt?: number; // timestamp in ms
  timerElapsedSeconds: number;
  isTimerRunning: boolean;
  matchCompletedAt?: string;
  
  // Boards
  boards: BoardScore[];
  maxBoards: number; // e.g. 3 boards max or 8 boards
  targetPoints?: number; // e.g. 29 points or 25 points
  
  // Official Results
  winnerId?: string;
  winnerName?: string;
  resultConfirmed: boolean;

  // Sets. A match with one set behaves exactly as a flat board list.
  numberOfSets?: number;
  player1SetsWon?: number;
  player2SetsWon?: number;
  /** The coin each side plays. Bound to the player id, not the screen side. */
  player1Color?: 'black' | 'white' | null;
  player2Color?: 'black' | 'white' | null;
  /** Display only — swapping never moves player1Id. */
  sidesSwapped?: boolean;
  tableNumber?: number | null;
  refereeId?: string | null;
  refereeName?: string | null;

  tieBreakRequired?: boolean;
  tieBreakRule?: string | null;
  tieBreakResult?: string | null;
  /** True when the match was awarded without being played. */
  walkover?: boolean;
  /** Why it was awarded — required when recording one, so it is always present. */
  walkoverReason?: string | null;
  resultConfirmedAt?: string;
  player1BoardWins: number;
  player2BoardWins: number;
  player1TotalPoints: number;
  player2TotalPoints: number;
  
  // Knockout linking
  nextMatchId?: string;
  nextMatchSlot?: 'player1' | 'player2';
  bracketPosition?: {
    round: number;
    matchIndex: number;
  };
  
  auditHistory: ScoreAuditLog[];
}

export interface TournamentRules {
  pointsForWin: number;
  pointsForDraw: number;
  pointsForLoss: number;
  maxBoardsPerMatch: number;
  targetScore: number; // 29 for standard carrom
  queenPoints: number; // usually 3 points
  /**
   * 'classic'         — each player keeps the coins they pocketed.
   * 'remaining_coins' — only the board winner scores, and scores the coins the
   *                     loser still had on the board (standard tournament carrom).
   */
  scoringMode?: 'classic' | 'remaining_coins';
  /** Coins per side, 9 in standard carrom. */
  coinsPerSide?: number;
  /** An uncovered queen returns to the board and scores nothing. */
  queenMustBeCovered?: boolean;
  /** Who the queen pays when the opponent covers it. */
  queenAwardTo?: 'coverer' | 'pocketer';
  /** How a tie is resolved once every board has been played. */
  tieBreak?: 'additional_board' | 'sudden_death' | 'most_board_wins' | 'organizer_decision';
  /** Carromite format: N sets of M boards, won on sets rather than points. */
  numberOfSets?: number;
  boardsPerSet?: number;
  /** What one coin is worth. 1 in standard carrom. */
  coinValue?: number;
  /** How a set is decided: on total points, or on boards won. */
  setWinnerRule?: 'total_points' | 'board_wins';
  /**
   * What the scorer is asked for on a board.
   * 'simple'   — who finished, and how many coins were left. Nothing else.
   * 'detailed' — adds the queen (pocketed by / covered by) and penalties.
   */
  boardEntryMode?: 'simple' | 'detailed';
  matchDurationMinutes: number;
  restTimeMinutes: number;
  /** 1 = one league for everyone; higher splits the league phase into groups. */
  groupCount?: number;
  /** How many from each group reach the knockout. */
  qualifiersPerGroup?: number;
  tiebreakerRules: ('points' | 'board_difference' | 'net_score_difference' | 'head_to_head')[];
}

export interface PosterConfig {
  themeStyle: 'emerald_gold' | 'royal_ebony' | 'heritage_wood' | 'championship_blue';
  tagline: string;
  highlights: string[];
  announcement: string;
  badgeText: string;
  customBgUrl?: string;
}

export interface Tournament {
  id: string;
  name: string;
  description: string;
  category: 'singles' | 'doubles' | 'both';
  format: TournamentFormat;
  status: TournamentStatus;
  registrationStartDate: string;
  registrationEndDate: string;
  tournamentStartDate: string;
  tournamentEndDate: string;
  venue: string;
  city: string;
  numberOfBoards: number;
  entryFee: number;
  prizePool: string;
  rules: TournamentRules;
  posterConfig: PosterConfig;
  createdAt: string;
  publishedAt?: string;

  // How it ended. Written by POST /complete and POST /cancel; absent from the
  // wire on a database without migration 012, null before the tournament has
  // reached either end, so a missing value is not "no champion yet".
  championId?: string | null;
  championName?: string | null;
  completedAt?: string | null;
  cancelledAt?: string | null;
  cancelReason?: string | null;

  // Attached items
  registrations: Registration[];
  matches: Match[];
  scheduledPublished: boolean;
  fixturesGenerated: boolean;
}

export interface StandingsRow {
  rank: number;
  participantId: string;
  participantName: string;
  played: number;
  won: number;
  lost: number;
  drawn: number;
  boardWins: number;
  boardLosses: number;
  boardDiff: number;
  boardDifference?: number;
  scoreFor: number;
  scoreAgainst: number;
  scoreDiff: number;
  netScoreDifference?: number;
  points: number;
  form: ('W' | 'L' | 'D')[];
  participantType?: 'singles' | 'doubles';
  isQualified?: boolean;
}

export interface TournamentNotification {
  id: string;
  tournamentId?: string;
  tournamentName?: string;
  title: string;
  message: string;
  timestamp: string;
  read: boolean;
  type: 'tournament_published' | 'registration_confirmed' | 'registration_closed' | 'schedule_published' | 'match_approaching' | 'result_confirmed' | 'knockout_advanced';
}

export interface AutomationMetric {
  title: string;
  value: number | string;
  change?: string;
  icon: string;
  automatedPercent?: number;
}

/** One points table: a whole category, or one group inside it. */
export interface StandingsBlock {
  group?: string;
  participantCount: number;
  matchCount: number;
  standings: StandingsRow[];
}

/** Points tables for a tournament, split by category and then by group. */
export interface StandingsCategory extends StandingsBlock {
  category: 'singles' | 'doubles';
  groups: StandingsBlock[];
}

export interface StandingsBreakdown {
  tournamentId: string;
  tournamentName?: string;
  format?: TournamentFormat;
  participantCount: number;
  categories: StandingsCategory[];
  standings: StandingsRow[];
}
