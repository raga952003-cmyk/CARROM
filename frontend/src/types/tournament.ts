export type TournamentFormat = 'round_robin' | 'knockout' | 'league_knockout';
export type MatchType = 'singles' | 'doubles';
export type TournamentStatus = 'draft' | 'registration_open' | 'registration_closed' | 'scheduled' | 'ongoing' | 'completed';
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

export interface BoardScore {
  boardNumber: number;
  status: BoardStatus;
  player1Score: number;
  player2Score: number;
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
  matchDurationMinutes: number;
  restTimeMinutes: number;
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
