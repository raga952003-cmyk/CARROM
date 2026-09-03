/**
 * Tournament Context
 * Communicates directly with the Python (FastAPI) Backend.
 * All operations are synchronized to the Supabase Postgres Database via the API layer.
 */

import React, { createContext, useContext, useState, useEffect, useRef, ReactNode } from 'react';
import { apiClient, AUTH_EXPIRED_EVENT } from '../utils/apiClient';
import { authService } from '../services/authService';
import { tournamentService } from '../services/tournamentService';
import { subscribeToTournamentData, RealtimeStatus } from '../services/realtimeService';
import { Resource, ALL_RESOURCES, resourcesToRefresh } from '../utils/refreshScope';
import { forgetFixtureFilters } from '../components/admin/FixtureScheduleView';
import {
  Tournament,
  Match,
  Player,
  Registration,
  TournamentNotification,
  StandingsRow,
  StandingsBreakdown,
  UserRole,
  Admin,
  BoardScore,
  Team,
  Side
} from '../types/tournament';

/**
 * One board as the umpire recorded it. The winner, the queen and the coins
 * left on the board are independent observations — the server scores them,
 * and none of them is derived from another.
 */
export interface BoardSubmission {
  /** Which set the board belongs to; omit for a single-set match. */
  setNumber?: number;
  p1Score: number;
  p2Score: number;
  boardWinner?: Side;
  p1CoinsPocketed?: number;
  p2CoinsPocketed?: number;
  coinsRemainingWith?: Side;
  coinsRemaining?: number;
  queenPocketedBy?: Side;
  queenCoveredBy?: Side;
  p1Penalty?: number;
  p2Penalty?: number;
  queenClaimedBy?: Side;
  queenCovered?: boolean;
  auditReason?: string;
}

interface TournamentContextType {
  // Config & state
  isConfigured: boolean;
  /** 'live' when Supabase Realtime is streaming; 'polling' is the degraded fallback. */
  realtimeStatus: RealtimeStatus;
  /** Set when the session ended on its own, so the sign-in screen can say why. */
  sessionNotice: string;
  clearSessionNotice: () => void;
  role: UserRole | null;
  setRole: (role: UserRole | null) => void;
  currentUser: Player | Admin | null;
  setCurrentUser: (user: Player | Admin | null) => void;
  allPlayers: Player[];
  allTeams: Team[];
  
  // Tournaments
  tournaments: Tournament[];
  activeTournamentId: string;
  setActiveTournamentId: (id: string) => void;
  currentTournament: Tournament | undefined;
  activeMatch: Match | null;
  setActiveMatch: (match: Match | null) => void;
  
  // Auth Operations
  /**
   * Register in the given role, and sign in as whatever comes back.
   *
   * Registration is open: the role travels with the request and the server
   * writes it, so the Administrator tab on the sign-up form really does make
   * an administrator.
   */
  signUpUser: (
    email: string,
    password: string,
    role: UserRole,
    metadata: Partial<Player>,
  ) => Promise<{ success: boolean; error?: string }>;
  signInUser: (email: string, password: string, role: UserRole) => Promise<{ success: boolean; error?: string }>;
  signOutUser: () => Promise<void>;
  
  // Admin Operations on Tournaments
  createTournament: (tournamentData: Partial<Tournament>) => Promise<string>;
  updateTournament: (id: string, updates: Partial<Tournament>) => Promise<void>;
  deleteTournament: (id: string) => Promise<void>;
  // The lifecycle. Each of these is one legal move on the server's state
  // machine; the server refuses (409, with the reason) when the tournament is
  // not where the move starts from, and callers show that reason.
  /** draft -> registration_open */
  publishTournament: (id: string) => Promise<void>;
  /** registration_open -> registration_closed */
  closeRegistration: (id: string) => Promise<void>;
  /** registration_closed or a published draw -> in_progress */
  startTournament: (id: string) => Promise<void>;
  /** in_progress -> completed, once every match is settled */
  finishTournament: (id: string) => Promise<void>;
  /** Any non-terminal state -> cancelled. The reason is required. */
  cancelTournament: (id: string, reason: string) => Promise<void>;
  /** Undo a confirmed result so a board can be corrected. Owner or manager only. */
  reopenMatch: (tournamentId: string, matchId: string, reason: string) => Promise<void>;
  generateFixturesForTournament: (id: string) => Promise<void>;
  generateScheduleForTournament: (id: string, restMinutes?: number) => Promise<void>;
  publishScheduleForTournament: (id: string) => Promise<void>;
  
  // Admin Operations on Players (Create and Maintain Player Accounts)
  createPlayerAccount: (playerData: Omit<Player, 'id'> & { id?: string }) => Promise<string>;
  updatePlayerAccount: (id: string, updates: Partial<Player>) => Promise<void>;
  deletePlayerAccount: (id: string) => Promise<void>;
  
  // Registration
  registerForTournament: (tournamentId: string, type: 'singles' | 'doubles', playerOrTeam: Player | Team | any) => Promise<boolean>;
  approveRegistration: (tournamentId: string, regId: string) => Promise<void>;
  rejectRegistration: (tournamentId: string, regId: string) => Promise<void>;
  
  // Match & Board Live Controls
  /** Add one fixture to a draw already in play, without redrawing it. */
  addManualMatch: (tournamentId: string, match: {
    stage: 'league' | 'knockout';
    roundName: string;
    player1Id: string;
    player2Id: string;
    boardNumber?: number;
    scheduledDate?: string;
    scheduledTime?: string;
  }) => Promise<void>;
  recordToss: (matchId: string, toss: {
    coinResult?: string | null;
    tossWinnerId?: string | null;
    tossWinnerName?: string | null;
    choice: string;
  }) => Promise<void>;
  startMatch: (tournamentId: string, matchId: string) => Promise<void>;
  pauseMatch: (tournamentId: string, matchId: string) => Promise<void>;
  resumeMatch: (tournamentId: string, matchId: string) => Promise<void>;
  addBoardToMatch: (tournamentId: string, matchId: string) => Promise<void>;
  updateBoardScore: (
    tournamentId: string, 
    matchId: string, 
    boardNumber: number, 
    boardData: Partial<BoardScore>,
    reason?: string,
    /** Required once a board is confirmed; the correction screen is that act. */
    override?: boolean
  ) => Promise<void>;
  submitBoardScore: (
    tournamentId: string,
    matchId: string,
    boardNumber: number,
    payload: BoardSubmission
  ) => Promise<void>;
  confirmMatchResult: (tournamentId: string, matchId: string) => Promise<void>;
  
  // Notifications
  notifications: TournamentNotification[];
  markNotificationAsRead: (id: string) => Promise<void>;
  markAllNotificationsAsRead: () => Promise<void>;
  addNotification: (title: string, message: string, type: TournamentNotification['type'], tournamentId?: string) => Promise<void>;
  
  // Utilities
  /** Re-read everything. Sign-in and first load; a mutation should not need it. */
  refreshData: () => Promise<void>;
  /**
   * Re-read the draw alone — tournaments with their entries, matches and
   * boards. What almost every admin action actually needs, and a quarter of
   * the requests refreshData() sends.
   */
  refreshTournaments: () => Promise<void>;
  refreshCurrentUser: () => Promise<void>;
  /** Points table computed server-side from official results (spec 74). */
  fetchStandings: (tournamentId: string) => Promise<StandingsRow[]>;
  /** The same tables split by category, and by group where one exists. */
  fetchStandingsBreakdown: (tournamentId: string) => Promise<StandingsBreakdown>;
}

/**
 * The auth endpoint types `role` as a plain string, while `Admin` and `Player`
 * each pin it to a literal. Narrow once here so the widening does not have to
 * be re-asserted at every place a signed-in user is stored.
 */
function toCurrentUser(user: { role: string } & Record<string, any>): Admin | Player {
  return (user.role === 'admin' ? user : { ...user, role: 'player' }) as Admin | Player;
}

const TournamentContext = createContext<TournamentContextType | undefined>(undefined);

export const TournamentProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [role, setRoleState] = useState<UserRole | null>(null);
  const [currentUser, setCurrentUserState] = useState<Player | Admin | null>(null);
  const [tournaments, setTournaments] = useState<Tournament[]>([]);
  const [allPlayers, setAllPlayers] = useState<Player[]>([]);
  const [allTeams, setAllTeams] = useState<Team[]>([]);
  const [notifications, setNotifications] = useState<TournamentNotification[]>([]);
  const [activeTournamentId, setActiveTournamentId] = useState<string>('');
  const [activeMatch, setActiveMatch] = useState<Match | null>(null);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [realtimeStatus, setRealtimeStatus] = useState<RealtimeStatus>('connecting');
  /** Explains an involuntary sign-out on the auth screen. */
  const [sessionNotice, setSessionNotice] = useState<string>('');

  const currentTournament = tournaments.find(t => t.id === activeTournamentId) || tournaments[0];

  // The poll interval is created once per auth change, so its closure would
  // otherwise capture the values of activeMatch/activeTournamentId from that
  // moment and never see later selections.
  const activeMatchRef = useRef<Match | null>(null);
  const activeTournamentIdRef = useRef<string>('');
  activeMatchRef.current = activeMatch;
  activeTournamentIdRef.current = activeTournamentId;

  // The four reads behind the screen, kept apart on purpose.
  //
  // They used to be one function: every admin action, and every realtime
  // change, re-read the tournaments AND the player directory AND the saved
  // teams AND the notifications. Scoring one board therefore cost four
  // requests, three of which could not have changed — and it replaced all four
  // state arrays, so every screen holding any of them re-rendered too. Now a
  // caller names only what its write could have touched.
  //
  // Per resource: the read in flight, the single follow-up queued behind it,
  // and when the most recent read was ISSUED — which is what tells an echo of
  // our own write apart from somebody else's change.
  const inFlight = useRef<Partial<Record<Resource, Promise<void>>>>({});
  const queued = useRef<Partial<Record<Resource, Promise<void>>>>({});
  const issuedAt = useRef<Partial<Record<Resource, number>>>({});
  const lastRefreshError = useRef<string>('');

  const reportRefreshFailure = (error: any) => {
    // A request torn down by navigation is not a failure, and an expired
    // session already signs out through AUTH_EXPIRED_EVENT.
    if (error?.isNavigationAbort) return;
    const message = error?.message || '';
    if (/sign in again|Not authenticated|expired/i.test(message)) return;
    // An outage reports itself on every cycle; say it once.
    if (message === lastRefreshError.current) return;
    lastRefreshError.current = message;
    console.error('Failed to refresh data from Python Backend:', error);
  };

  const readResource = async (resource: Resource): Promise<void> => {
    // Held until the read comes back. The stamp says "a read that SUCCEEDED
    // was issued at this instant", and only a successful read can be said to
    // have seen anything: stamping on the way out meant a request that then
    // failed still suppressed the realtime event for the change it missed, and
    // on a live connection there is no poll behind it — the change stayed off
    // the screen for good.
    const startedAt = Date.now();
    switch (resource) {
      case 'tournaments': {
        const tournamentsData = await tournamentService.getAllTournaments();
        setTournaments(tournamentsData);
        if (tournamentsData.length > 0 && !activeTournamentIdRef.current) {
          setActiveTournamentId(tournamentsData[0].id);
        }
        // Reload the open match from the data just fetched.
        const openMatch = activeMatchRef.current;
        if (openMatch) {
          const freshT = tournamentsData.find(t => t.id === openMatch.tournamentId);
          const freshM = freshT?.matches?.find(m => m.id === openMatch.id);
          if (freshM) setActiveMatch(freshM);
        }
        break;
      }
      case 'players':
        setAllPlayers(await apiClient.get<Player[]>('/players'));
        break;
      case 'teams':
        setAllTeams((await tournamentService.getTeams()) as Team[]);
        break;
      case 'notifications':
        setNotifications(await apiClient.get<TournamentNotification[]>('/notifications'));
        break;
    }
    issuedAt.current[resource] = startedAt;
    lastRefreshError.current = '';
  };

  const runResource = (resource: Resource): Promise<void> => {
    // Someone who asks while a read is already running cannot use that read's
    // answer: it was issued BEFORE whatever they just changed.
    //
    // Handing it to them anyway is what made a paused timer come back still
    // running — the write had landed, the read that reported it had not been
    // issued yet. That reads as the button having done nothing, so the umpire
    // taps again, and joins the same stale promise.
    //
    // So: wait for the run in flight, then do exactly one more, and let
    // everyone who arrived in the meantime share that single follow-up. Still
    // at most two requests per resource however many callers pile up.
    const running = inFlight.current[resource];
    if (running) {
      let follow = queued.current[resource];
      if (!follow) {
        follow = running
          .catch(() => undefined)
          .then(() => {
            queued.current[resource] = undefined;
            return runResource(resource);
          });
        queued.current[resource] = follow;
      }
      return follow;
    }
    const run = readResource(resource)
      .catch(reportRefreshFailure)
      .finally(() => { inFlight.current[resource] = undefined; });
    inFlight.current[resource] = run;
    return run;
  };

  /** Re-read the named resources together, and wait for all of them. */
  const refresh = (resources: Resource[] = ALL_RESOURCES): Promise<void> =>
    Promise.all(resources.map(runResource)).then(() => undefined);

  const refreshData = (): Promise<void> => refresh();
  const refreshTournaments = (): Promise<void> => refresh(['tournaments']);

  // A dead session must end the session in the app too. Previously the token
  // was cleared but isAuthenticated stayed true, so the refresh loop kept
  // firing against an empty token and produced an unbounded stream of 401s.
  useEffect(() => {
    const onExpired = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      setIsAuthenticated(false);
      setCurrentUserState(null);
      setRoleState(null);
      setTournaments([]);
      setAllPlayers([]);
      setAllTeams([]);
      setNotifications([]);
      setActiveMatch(null);
      setSessionNotice(typeof detail === 'string' ? detail : 'Your session has expired. Please sign in again.');
    };

    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  }, []);

  // Renew the access token shortly before it expires, so an open dashboard
  // does not die mid-session -- and while we are asking, check who we still
  // are.
  //
  // The role only ever came from the sign-in response, and nothing re-read it.
  // So an organiser promoting somebody mid-event changed nothing on that
  // person's screen: they went on seeing the player dashboard until they
  // signed out and back in, with no way to know they had been promoted. A
  // demotion was worse -- the admin screens stayed up, offering controls the
  // server had already started refusing.
  useEffect(() => {
    if (!isAuthenticated) return;

    const tick = async () => {
      const remaining = authService.secondsUntilExpiry();
      if (remaining !== null && remaining < 120) {
        await authService.refresh();
      }
      // Cheap, once a minute, and it fails quietly: a blip must not sign
      // anyone out in the middle of a tournament.
      await refreshCurrentUser();
    };

    tick();
    const timer = setInterval(tick, 60000);
    return () => clearInterval(timer);
  }, [isAuthenticated]);

  const refreshCurrentUser = async () => {
    try {
      const user = await authService.getCurrentUser();
      setCurrentUserState(toCurrentUser(user));
      setRoleState(user.role as UserRole);
    } catch {
      // A failed refresh must not sign anyone out mid-tournament; the existing
      // user object stays, and the next real request will surface any problem.
    }
  };

  // Sync auth on mount
  useEffect(() => {
    const checkAuth = async () => {
      try {
        const user = await authService.getCurrentUser();
        setCurrentUserState(toCurrentUser(user));
        setRoleState(user.role as UserRole);
        setIsAuthenticated(true);
      } catch (error) {
        setIsAuthenticated(false);
        setCurrentUserState(null);
        setRoleState(null);
      }
    };

    if (authService.isAuthenticated()) {
      checkAuth();
    }
  }, []);

  // Live updates over Supabase Realtime (spec 72/73). Spec 91 rules out the
  // previous 5-second poll loop; the slow interval below only runs when the
  // websocket is unavailable, as a safety net rather than the primary channel.
  useEffect(() => {
    if (!isAuthenticated) return;

    let fallbackTimer: ReturnType<typeof setInterval> | null = null;

    const stopFallback = () => {
      if (fallbackTimer) {
        clearInterval(fallbackTimer);
        fallbackTimer = null;
      }
    };

    const handle = subscribeToTournamentData({
      onChange: ({ tables, observedAt }) => {
        // Re-read only what moved, and not the echo of our own write. Both
        // decisions live in refreshScope.ts, where they can be tested.
        const stale = resourcesToRefresh(tables, observedAt, issuedAt.current);
        if (stale.length) refresh(stale);
      },
      onStatus: (status) => {
        setRealtimeStatus(status);
        if (status === 'live') {
          stopFallback();
        } else if (!fallbackTimer) {
          // Degraded mode only: a 30s heartbeat, not a 5s poll.
          fallbackTimer = setInterval(refreshData, 30000);
        }
      },
    });

    refreshData();

    return () => {
      handle.unsubscribe();
      stopFallback();
    };
  }, [isAuthenticated]);

  // Auth Operations
  const signUpUser = async (
    email: string,
    password: string,
    selectedRole: UserRole,
    metadata: Partial<Player>,
  ): Promise<{ success: boolean; error?: string }> => {
    try {
      const response = await authService.signUp({
        email,
        password,
        name: metadata.name || 'User',
        club: metadata.club,
        city: metadata.city,
        phone: metadata.phone,
        rating: metadata.rating || 1500,
        // The role the form asked for. It used to be taken here and then
        // dropped on the floor -- the form offered Administrator, the request
        // never mentioned it, and the new account was signed straight in as a
        // player with nothing said about it.
        role: selectedRole === 'admin' ? 'admin' : 'player',
      });

      // Still read back from the server rather than assumed: it is the one
      // that decides, and a refusal must not leave the app believing
      // otherwise.
      setCurrentUserState(toCurrentUser(response.user));
      setRoleState(response.user.role as UserRole);
      setIsAuthenticated(true);
      await refreshData();
      return { success: true };
    } catch (error: any) {
      return { success: false, error: error.message || 'Signup failed' };
    }
  };

  const signInUser = async (
    email: string, 
    password: string, 
    selectedRole: UserRole
  ): Promise<{ success: boolean; error?: string }> => {
    try {
      const response = await authService.login({
        email,
        password,
        role: selectedRole
      });
      
      setCurrentUserState(toCurrentUser(response.user));
      setRoleState(response.user.role as UserRole);
      setIsAuthenticated(true);
      setSessionNotice('');
      await refreshData();
      return { success: true };
    } catch (error: any) {
      return { success: false, error: error.message || 'Login failed' };
    }
  };

  const signOutUser = async () => {
    authService.logout();
    forgetFixtureFilters();
    setCurrentUserState(null);
    setRoleState(null);
    setIsAuthenticated(false);
    setTournaments([]);
    setAllPlayers([]);
    setAllTeams([]);
    setNotifications([]);
    setActiveMatch(null);
  };

  // Tournament operations
  const createTournament = async (tournamentData: Partial<Tournament>): Promise<string> => {
    const response = await tournamentService.createTournament(tournamentData);
    await refresh(['tournaments']);
    return response.id;
  };

  const updateTournament = async (id: string, updates: Partial<Tournament>) => {
    await tournamentService.updateTournament(id, updates);
    await refresh(['tournaments']);
  };

  const deleteTournament = async (id: string) => {
    await tournamentService.deleteTournament(id);
    await refresh(['tournaments']);
  };

  // The lifecycle goes through its own verbs, not PUT {status}. Writing the
  // word directly changed nothing but the word: a tournament could be started
  // without a draw, finished with matches still open, and nobody was told.
  // The verbs do the work each move implies -- /complete records the champion
  // and refuses while anything is unfinished, /cancel puts the reason on the
  // record -- and the errors they raise carry the explanation, so they are
  // left to propagate for the screen to show.
  const publishTournament = async (id: string) => {
    await tournamentService.openRegistration(id);
    await refresh(['tournaments']);
  };

  const closeRegistration = async (id: string) => {
    await tournamentService.closeRegistration(id);
    await refresh(['tournaments']);
  };

  const startTournament = async (id: string) => {
    await tournamentService.startTournament(id);
    await refresh(['tournaments']);
  };

  const finishTournament = async (id: string) => {
    await tournamentService.completeTournament(id);
    await refresh(['tournaments', 'notifications']);
  };

  const cancelTournament = async (id: string, reason: string) => {
    await tournamentService.cancelTournament(id, reason);
    // Calling a tournament off tells everyone entered, the organiser included.
    await refresh(['tournaments', 'notifications']);
  };

  // tournamentId is taken for symmetry with the other match operations; the
  // API addresses the match alone.
  const reopenMatch = async (tournamentId: string, matchId: string, reason: string) => {
    await tournamentService.reopenMatch(matchId, reason);
    // Reopening a result is announced to both players and to the organisers.
    await refresh(['tournaments', 'notifications']);
  };

  const generateFixturesForTournament = async (id: string) => {
    await apiClient.post(`/tournaments/${id}/fixtures`, {});
    await refresh(['tournaments']);
  };

  const generateScheduleForTournament = async (id: string, restMinutes: number = 10) => {
    await apiClient.post(`/tournaments/${id}/schedule?restMinutes=${restMinutes}`, {});
    await refresh(['tournaments']);
  };

  const publishScheduleForTournament = async (id: string) => {
    await apiClient.post(`/tournaments/${id}/publish-schedule`, {});
    await refresh(['tournaments', 'notifications']);
  };

  // Player directory operations
  const createPlayerAccount = async (playerData: Omit<Player, 'id'> & { id?: string }): Promise<string> => {
    const response = await apiClient.post<Player>('/players', playerData);
    await refresh(['players']);
    return response.id;
  };

  const updatePlayerAccount = async (id: string, updates: Partial<Player>) => {
    await apiClient.put(`/players/${id}`, updates);
    // Entries and teams carry the joined profile row, not just its id, so a
    // rename that only re-read the directory left the old name on every
    // fixture and every entry until something else reloaded the draw.
    await refresh(['players', 'tournaments']);
  };

  const deletePlayerAccount = async (id: string) => {
    await apiClient.delete(`/players/${id}`);
    // A deleted player disappears from the draw and its entries too.
    await refresh(['players', 'tournaments', 'teams']);
  };

  // Registrations
  const registerForTournament = async (
    tournamentId: string,
    type: 'singles' | 'doubles',
    playerOrTeam: any
  ): Promise<boolean> => {
    try {
      const isTeam = type === 'doubles' && playerOrTeam && 'player1' in playerOrTeam;
      const partner = isTeam ? playerOrTeam.player2 : null;

      // Callers may build a partner object with a placeholder id for someone who
      // has no account yet. Only a real profile id (a UUID) may be sent as
      // partner_id, otherwise the backend would fail looking it up.
      const isProfileId = (value?: string) =>
        !!value && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);

      const payload: Record<string, any> = {
        type,
        player_id: isTeam ? playerOrTeam.player1?.id : playerOrTeam.id,
        team_name: isTeam ? playerOrTeam.name || null : null,
        // An existing partner is sent by id; a brand-new one by details, and
        // the backend creates their profile.
        partner_id: isProfileId(partner?.id) ? partner.id : null,
        partner_name: partner?.name || null,
        partner_phone: partner?.phone || null,
        partner_email: partner?.email || null,
      };

      await tournamentService.registerForTournament(tournamentId, payload);
      await refresh(['tournaments', 'teams', 'players']);
      return true;
    } catch (e: any) {
      console.error('Registration failed:', e);
      throw e instanceof Error ? e : new Error('Registration failed.');
    }
  };

  const approveRegistration = async (tournamentId: string, regId: string) => {
    await tournamentService.approveRegistration(regId);
    await refresh(['tournaments']);
  };

  const rejectRegistration = async (tournamentId: string, regId: string) => {
    await tournamentService.rejectRegistration(regId);
    await refresh(['tournaments']);
  };

  // Match operations
  const addManualMatch = async (tournamentId: string, match: any) => {
    await apiClient.post(`/tournaments/${tournamentId}/matches`, match);
    await refresh(['tournaments']);
  };

  const recordToss = async (matchId: string, toss: any) => {
    await apiClient.post(`/matches/${matchId}/toss`, toss);
  };

  const startMatch = async (tournamentId: string, matchId: string) => {
    await apiClient.post(`/matches/${matchId}/start`, {});
    await refresh(['tournaments']);
  };

  const pauseMatch = async (tournamentId: string, matchId: string) => {
    await apiClient.post(`/matches/${matchId}/pause`, {});
    await refresh(['tournaments']);
  };

  const resumeMatch = async (tournamentId: string, matchId: string) => {
    await apiClient.post(`/matches/${matchId}/resume`, {});
    await refresh(['tournaments']);
  };

  const addBoardToMatch = async (tournamentId: string, matchId: string) => {
    await apiClient.post(`/matches/${matchId}/boards`, {});
    await refresh(['tournaments']);
  };

  const updateBoardScore = async (
    tournamentId: string, 
    matchId: string, 
    boardNumber: number, 
    boardData: Partial<BoardScore>, 
    reason: string = 'Score update',
    override: boolean = false
  ) => {
    await apiClient.put(
      `/matches/${matchId}/boards/${boardNumber}?reason=${encodeURIComponent(reason)}`
      + (override ? '&override=true' : ''),
      boardData
    );
    await refresh(['tournaments']);
  };

  const submitBoardScore = async (
    tournamentId: string,
    matchId: string,
    boardNumber: number,
    payload: BoardSubmission
  ) => {
    await apiClient.post(`/matches/${matchId}/boards/${boardNumber}/submit`, {
      auditReason: 'Board score finalized',
      ...payload,
    });
    await refresh(['tournaments']);
  };

  const confirmMatchResult = async (tournamentId: string, matchId: string) => {
    await apiClient.post(`/matches/${matchId}/confirm`, {});
    // Confirming can fill the next knockout round and notify both players.
    await refresh(['tournaments', 'notifications']);
  };

  // Notifications
  const markNotificationAsRead = async (id: string) => {
    await apiClient.put(`/notifications/${id}/read`, {});
    await refresh(['notifications']);
  };

  const markAllNotificationsAsRead = async () => {
    await apiClient.put(`/notifications/read-all`, {});
    await refresh(['notifications']);
  };

  const addNotification = async (
    title: string, 
    message: string, 
    type: TournamentNotification['type'], 
    tournamentId?: string
  ) => {
    const payload = {
      title,
      message,
      type,
      tournamentId
    };
    await apiClient.post('/notifications', payload);
    await refresh(['notifications']);
  };

  const fetchStandings = async (tournamentId: string): Promise<StandingsRow[]> => {
    const result = await tournamentService.getStandings(tournamentId);
    return result.standings || [];
  };

  const fetchStandingsBreakdown = async (tournamentId: string): Promise<StandingsBreakdown> => {
    return tournamentService.getStandings(tournamentId);
  };

  return (
    <TournamentContext.Provider
      value={{
        isConfigured: true,
        realtimeStatus,
        sessionNotice,
        clearSessionNotice: () => setSessionNotice(''),
        role,
        setRole: setRoleState,
        currentUser,
        setCurrentUser: setCurrentUserState,
        allPlayers,
        allTeams,
        tournaments,
        activeTournamentId,
        setActiveTournamentId,
        currentTournament,
        activeMatch,
        setActiveMatch,
        signUpUser,
        signInUser,
        signOutUser,
        refreshCurrentUser,
        createTournament,
        updateTournament,
        deleteTournament,
        publishTournament,
        closeRegistration,
        startTournament,
        finishTournament,
        cancelTournament,
        reopenMatch,
        generateFixturesForTournament,
        generateScheduleForTournament,
        publishScheduleForTournament,
        createPlayerAccount,
        updatePlayerAccount,
        deletePlayerAccount,
        registerForTournament,
        approveRegistration,
        rejectRegistration,
        addManualMatch,
        recordToss,
        startMatch,
        pauseMatch,
        resumeMatch,
        addBoardToMatch,
        updateBoardScore,
        submitBoardScore,
        confirmMatchResult,
        notifications,
        markNotificationAsRead,
        markAllNotificationsAsRead,
        addNotification,
        refreshData,
        refreshTournaments,
        fetchStandings,
        fetchStandingsBreakdown
      }}
    >
      {children}
    </TournamentContext.Provider>
  );
};

export const useTournament = () => {
  const context = useContext(TournamentContext);
  if (!context) {
    throw new Error('useTournament must be used within a TournamentProvider');
  }
  return context;
};
