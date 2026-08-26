/**
 * Tournament Context
 * Communicates directly with the Python (FastAPI) Backend.
 * All operations are synchronized to the Supabase Postgres Database via the API layer.
 */

import React, { createContext, useContext, useState, useEffect, useRef, ReactNode } from 'react';
import { apiClient } from '../utils/apiClient';
import { authService } from '../services/authService';
import { tournamentService } from '../services/tournamentService';
import { subscribeToTournamentData, RealtimeStatus } from '../services/realtimeService';
import { 
  Tournament, 
  Match, 
  Player, 
  Registration,
  TournamentNotification,
  StandingsRow,
  UserRole,
  Admin,
  BoardScore,
  Team
} from '../types/tournament';

interface TournamentContextType {
  // Config & state
  isConfigured: boolean;
  /** 'live' when Supabase Realtime is streaming; 'polling' is the degraded fallback. */
  realtimeStatus: RealtimeStatus;
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
  signUpUser: (email: string, password: string, role: UserRole, metadata: Partial<Player>) => Promise<{ success: boolean; error?: string }>;
  signInUser: (email: string, password: string, role: UserRole) => Promise<{ success: boolean; error?: string }>;
  signOutUser: () => Promise<void>;
  
  // Admin Operations on Tournaments
  createTournament: (tournamentData: Partial<Tournament>) => Promise<string>;
  updateTournament: (id: string, updates: Partial<Tournament>) => Promise<void>;
  deleteTournament: (id: string) => Promise<void>;
  publishTournament: (id: string) => Promise<void>;
  closeRegistration: (id: string) => Promise<void>;
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
  startMatch: (tournamentId: string, matchId: string) => Promise<void>;
  pauseMatch: (tournamentId: string, matchId: string) => Promise<void>;
  resumeMatch: (tournamentId: string, matchId: string) => Promise<void>;
  addBoardToMatch: (tournamentId: string, matchId: string) => Promise<void>;
  updateBoardScore: (
    tournamentId: string, 
    matchId: string, 
    boardNumber: number, 
    boardData: Partial<BoardScore>, 
    reason?: string
  ) => Promise<void>;
  submitBoardScore: (
    tournamentId: string, 
    matchId: string, 
    boardNumber: number, 
    p1Score: number, 
    p2Score: number, 
    queenClaimedBy?: 'player1' | 'player2' | 'none',
    queenCovered?: boolean,
    auditReason?: string
  ) => Promise<void>;
  confirmMatchResult: (tournamentId: string, matchId: string) => Promise<void>;
  
  // Notifications
  notifications: TournamentNotification[];
  markNotificationAsRead: (id: string) => Promise<void>;
  markAllNotificationsAsRead: () => Promise<void>;
  addNotification: (title: string, message: string, type: TournamentNotification['type'], tournamentId?: string) => Promise<void>;
  
  // Utilities
  resetToSampleData: () => void;
  refreshData: () => Promise<void>;
  /** Points table computed server-side from official results (spec 74). */
  fetchStandings: (tournamentId: string) => Promise<StandingsRow[]>;
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

  const currentTournament = tournaments.find(t => t.id === activeTournamentId) || tournaments[0];

  // The poll interval is created once per auth change, so its closure would
  // otherwise capture the values of activeMatch/activeTournamentId from that
  // moment and never see later selections.
  const activeMatchRef = useRef<Match | null>(null);
  const activeTournamentIdRef = useRef<string>('');
  activeMatchRef.current = activeMatch;
  activeTournamentIdRef.current = activeTournamentId;

  const refreshData = async () => {
    try {
      // 1. Fetch Tournaments
      const tournamentsData = await tournamentService.getAllTournaments();
      setTournaments(tournamentsData);
      
      if (tournamentsData.length > 0 && !activeTournamentIdRef.current) {
        setActiveTournamentId(tournamentsData[0].id);
      }

      // 2. Fetch Players Directory
      const playersData = await apiClient.get<Player[]>('/players');
      setAllPlayers(playersData);

      // 2b. Fetch doubles teams so partner pickers have something to show
      const teamsData = await tournamentService.getTeams();
      setAllTeams(teamsData as Team[]);

      // 3. Fetch Notifications
      const notificationsData = await apiClient.get<TournamentNotification[]>('/notifications');
      setNotifications(notificationsData);

      // 4. Reload active match if it exists
      const openMatch = activeMatchRef.current;
      if (openMatch) {
        const freshT = tournamentsData.find(t => t.id === openMatch.tournamentId);
        const freshM = freshT?.matches?.find(m => m.id === openMatch.id);
        if (freshM) {
          setActiveMatch(freshM);
        }
      }
    } catch (error) {
      console.error('Failed to refresh data from Python Backend:', error);
    }
  };

  // Sync auth on mount
  useEffect(() => {
    const checkAuth = async () => {
      try {
        const user = await authService.getCurrentUser();
        setCurrentUserState(user);
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
      onChange: () => { refreshData(); },
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
    metadata: Partial<Player>
  ): Promise<{ success: boolean; error?: string }> => {
    try {
      const response = await authService.signUp({
        email,
        password,
        name: metadata.name || 'User',
        role: selectedRole,
        club: metadata.club,
        city: metadata.city,
        phone: metadata.phone,
        rating: metadata.rating || 1500
      });
      
      setCurrentUserState(response.user);
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
      
      setCurrentUserState(response.user);
      setRoleState(response.user.role as UserRole);
      setIsAuthenticated(true);
      await refreshData();
      return { success: true };
    } catch (error: any) {
      return { success: false, error: error.message || 'Login failed' };
    }
  };

  const signOutUser = async () => {
    authService.logout();
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
    await refreshData();
    return response.id;
  };

  const updateTournament = async (id: string, updates: Partial<Tournament>) => {
    await tournamentService.updateTournament(id, updates);
    await refreshData();
  };

  const deleteTournament = async (id: string) => {
    await tournamentService.deleteTournament(id);
    await refreshData();
  };

  const publishTournament = async (id: string) => {
    await tournamentService.updateTournament(id, { status: 'registration_open' });
    await refreshData();
  };

  const closeRegistration = async (id: string) => {
    await tournamentService.updateTournament(id, { status: 'registration_closed' });
    await refreshData();
  };

  const generateFixturesForTournament = async (id: string) => {
    await apiClient.post(`/tournaments/${id}/fixtures`, {});
    await refreshData();
  };

  const generateScheduleForTournament = async (id: string, restMinutes: number = 10) => {
    await apiClient.post(`/tournaments/${id}/schedule?restMinutes=${restMinutes}`, {});
    await refreshData();
  };

  const publishScheduleForTournament = async (id: string) => {
    await apiClient.post(`/tournaments/${id}/publish-schedule`, {});
    await refreshData();
  };

  // Player directory operations
  const createPlayerAccount = async (playerData: Omit<Player, 'id'> & { id?: string }): Promise<string> => {
    const response = await apiClient.post<Player>('/players', playerData);
    await refreshData();
    return response.id;
  };

  const updatePlayerAccount = async (id: string, updates: Partial<Player>) => {
    await apiClient.put(`/players/${id}`, updates);
    await refreshData();
  };

  const deletePlayerAccount = async (id: string) => {
    await apiClient.delete(`/players/${id}`);
    await refreshData();
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
      await refreshData();
      return true;
    } catch (e: any) {
      console.error('Registration failed:', e);
      throw e instanceof Error ? e : new Error('Registration failed.');
    }
  };

  const approveRegistration = async (tournamentId: string, regId: string) => {
    await tournamentService.approveRegistration(regId);
    await refreshData();
  };

  const rejectRegistration = async (tournamentId: string, regId: string) => {
    await tournamentService.rejectRegistration(regId);
    await refreshData();
  };

  // Match operations
  const startMatch = async (tournamentId: string, matchId: string) => {
    await apiClient.post(`/matches/${matchId}/start`, {});
    await refreshData();
  };

  const pauseMatch = async (tournamentId: string, matchId: string) => {
    await apiClient.post(`/matches/${matchId}/pause`, {});
    await refreshData();
  };

  const resumeMatch = async (tournamentId: string, matchId: string) => {
    await apiClient.post(`/matches/${matchId}/resume`, {});
    await refreshData();
  };

  const addBoardToMatch = async (tournamentId: string, matchId: string) => {
    await apiClient.post(`/matches/${matchId}/boards`, {});
    await refreshData();
  };

  const updateBoardScore = async (
    tournamentId: string, 
    matchId: string, 
    boardNumber: number, 
    boardData: Partial<BoardScore>, 
    reason: string = 'Score update'
  ) => {
    await apiClient.put(`/matches/${matchId}/boards/${boardNumber}?reason=${encodeURIComponent(reason)}`, boardData);
    await refreshData();
  };

  const submitBoardScore = async (
    tournamentId: string, 
    matchId: string, 
    boardNumber: number, 
    p1Score: number, 
    p2Score: number, 
    queenClaimedBy: 'player1' | 'player2' | 'none' = 'none',
    queenCovered: boolean = false,
    auditReason: string = 'Board score finalized'
  ) => {
    const payload = {
      p1Score,
      p2Score,
      queenClaimedBy,
      queenCovered,
      auditReason
    };
    await apiClient.post(`/matches/${matchId}/boards/${boardNumber}/submit`, payload);
    await refreshData();
  };

  const confirmMatchResult = async (tournamentId: string, matchId: string) => {
    await apiClient.post(`/matches/${matchId}/confirm`, {});
    await refreshData();
  };

  // Notifications
  const markNotificationAsRead = async (id: string) => {
    await apiClient.put(`/notifications/${id}/read`, {});
    await refreshData();
  };

  const markAllNotificationsAsRead = async () => {
    await apiClient.put(`/notifications/read-all`, {});
    await refreshData();
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
    await refreshData();
  };

  const fetchStandings = async (tournamentId: string): Promise<StandingsRow[]> => {
    const result = await tournamentService.getStandings(tournamentId);
    return result.standings || [];
  };

  const resetToSampleData = () => {
    alert("Resetting sample data is disabled when connected to the Python API Backend server. Please manage entries through the dashboard interface.");
  };

  return (
    <TournamentContext.Provider
      value={{
        isConfigured: true,
        realtimeStatus,
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
        createTournament,
        updateTournament,
        deleteTournament,
        publishTournament,
        closeRegistration,
        generateFixturesForTournament,
        generateScheduleForTournament,
        publishScheduleForTournament,
        createPlayerAccount,
        updatePlayerAccount,
        deletePlayerAccount,
        registerForTournament,
        approveRegistration,
        rejectRegistration,
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
        resetToSampleData,
        refreshData,
        fetchStandings
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
