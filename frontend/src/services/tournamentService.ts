/**
 * Tournament Service
 * Handles all tournament-related API calls
 */

import { apiClient } from '../utils/apiClient';
import { Tournament, Match, StandingsBreakdown } from '../types/tournament';

/**
 * Put the zeroes back on the boards nobody has played yet.
 *
 * An unplayed board is eight identical zeroes, and there were 1520 of them in
 * this tournament: 1.4 MB and 5.7 seconds on every load, and again on every
 * realtime change — so each board an umpire scored made every other screen
 * re-download the entire draw. The list response now sends an unplayed board
 * as its identity alone — which set, which board, what state — and the zeroes
 * are put back here.
 *
 * It used to send no unplayed board at all, only a count, and rebuild boards
 * 1..n from it. That flattened every multi-set match: board numbers restart at
 * 1 in each set, so three sets of four arrived as one set of twelve, sets two
 * and three had nothing left to score, and submitting board 5 of set 1 hit a
 * board that does not exist. A count cannot say which set a board is in.
 *
 * Done at the edge on purpose: every component downstream goes on seeing a
 * complete `boards` array, so nothing else has to know this happens. Nothing
 * addresses a board by id — submission is by set and board NUMBER — so a
 * filled-in board is indistinguishable from one that travelled whole.
 */
function fillBoards(t: Tournament): Tournament {
  if (!t?.matches?.length) return t;
  return {
    ...t,
    matches: t.matches.map(m => {
      const sent = m.boards || [];
      // A stub carries no score. Anything that does is already complete.
      if (!sent.some(b => b.player1Score === undefined)) return m;
      const boards = sent.map(b =>
        b.player1Score === undefined
          ? ({
              player1Score: 0,
              player2Score: 0,
              queenClaimedBy: 'none',
              queenCovered: false,
              ...b,
            } as any)
          : b
      );
      return { ...m, boards };
    }),
  };
}

export const tournamentService = {
  /**
   * Get all tournaments
   */
  async getAllTournaments() {
    const list = await apiClient.get<Tournament[]>('/tournaments');
    return (list || []).map(fillBoards);
  },

  /**
   * Get tournament by ID
   */
  async getTournamentById(id: string) {
    // Same read shape as the list: unplayed boards arrive as identity alone.
    // The print sheets read through here, and were counting boards.
    return fillBoards(await apiClient.get<Tournament>(`/tournaments/${id}`));
  },

  /**
   * Create tournament
   */
  async createTournament(data: Partial<Tournament>) {
    return apiClient.post<Tournament>('/tournaments', data);
  },

  /**
   * Update tournament
   */
  async updateTournament(id: string, data: Partial<Tournament>) {
    return apiClient.put<Tournament>(`/tournaments/${id}`, data);
  },

  /**
   * Delete tournament
   */
  async deleteTournament(id: string) {
    return apiClient.delete(`/tournaments/${id}`);
  },

  // The lifecycle, as verbs rather than as a status written through PUT.
  //
  // PUT /tournaments/{id} with {status} could change the word but nothing
  // else: it did not check that a draw existed before play began, did not
  // record who won, and told nobody. Each of these does the work its state
  // change implies, and refuses (409) with a reason when the move is not
  // legal from where the tournament stands -- /complete's refusal lists the
  // matches that are still unfinished, which is what the organiser needs to
  // see, so callers should surface the message rather than swallow it.

  /** draft | registration_closed -> registration_open */
  async openRegistration(id: string) {
    return apiClient.post<Tournament>(`/tournaments/${id}/open-registration`, {});
  },

  /** registration_open -> registration_closed */
  async closeRegistration(id: string) {
    return apiClient.post<Tournament>(`/tournaments/${id}/close-registration`, {});
  },

  /** registration_closed | fixture_* -> in_progress. 409 without a draw. */
  async startTournament(id: string) {
    return apiClient.post<Tournament>(`/tournaments/${id}/start`, {});
  },

  /** in_progress -> completed. 409 until every match is confirmed, a walkover or cancelled. */
  async completeTournament(id: string) {
    return apiClient.post<Tournament>(`/tournaments/${id}/complete`, {});
  },

  /** Any non-terminal state -> cancelled. The reason goes on the record and to every participant. */
  async cancelTournament(id: string, reason: string) {
    return apiClient.post<Tournament>(`/tournaments/${id}/cancel`, { reason });
  },

  /**
   * Take a confirmed match back to live so a board can be corrected. Owner or
   * manager only, with a stated reason; refused once the match it fed has
   * been played.
   */
  async reopenMatch(matchId: string, reason: string) {
    return apiClient.post<Match>(`/matches/${matchId}/reopen`, { reason });
  },

  /**
   * Get registrations for tournament
   */
  async getRegistrations(tournamentId: string) {
    return apiClient.get(`/tournaments/${tournamentId}/registrations`);
  },

  /**
   * Register for tournament
   */
  async registerForTournament(tournamentId: string, data: any) {
    return apiClient.post(`/tournaments/${tournamentId}/registrations`, data);
  },

  /**
   * Parse a pasted player list server-side. The AI key stays on the server.
   */
  async parseParticipantsWithAI(text: string) {
    return apiClient.post<{ available: boolean; players: any[]; error?: string }>(
      '/ai/parse-participants', { text }
    );
  },

  /**
   * Generate poster copy server-side.
   */
  async generatePosterCopy(payload: Record<string, any>) {
    return apiClient.post<any>('/ai/poster-copy', payload);
  },

  /**
   * All doubles teams, optionally limited to one tournament's entrants
   */
  async getTeams(tournamentId?: string) {
    const query = tournamentId ? `?tournamentId=${tournamentId}` : '';
    return apiClient.get<any[]>(`/teams${query}`);
  },

  /**
   * Pair two existing players into a team (reuses the pair if it exists)
   */
  async createTeam(data: { name?: string; player1_id: string; player2_id: string; club?: string; city?: string; seed?: number }) {
    return apiClient.post<any>('/teams', data);
  },

  /**
   * Server-computed points table (spec 74). The frontend renders this; it must
   * not derive standings itself.
   */
  async getStandings(tournamentId: string) {
    return apiClient.get<StandingsBreakdown>(`/standings/${tournamentId}`);
  },

  /**
   * Top N of the league table (group-stage qualification cut)
   */
  async getQualified(tournamentId: string, count: number = 4) {
    return apiClient.get(`/standings/${tournamentId}/qualified?count=${count}`);
  },

  /**
   * Committed schedule plus any detected conflicts
   */
  async getSchedule(tournamentId: string) {
    return apiClient.get(`/scheduling/${tournamentId}`);
  },

  /**
   * Approve a pending registration (admin only)
   */
  async approveRegistration(registrationId: string) {
    return apiClient.post(`/registrations/${registrationId}/approve`, {});
  },

  /**
   * Reject a pending registration (admin only)
   */
  async rejectRegistration(registrationId: string) {
    return apiClient.post(`/registrations/${registrationId}/reject`, {});
  },

  /**
   * Upload and parse Excel/CSV participant sheet on backend
   */
  async uploadExcel(file: File) {
    const { API_BASE_URL } = await import('../utils/apiClient');
    const formData = new FormData();
    formData.append('file', file);

    const token = localStorage.getItem('auth_token');
    const headers: HeadersInit = {};
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(`${API_BASE_URL}/imports/excel`, {
      method: 'POST',
      headers,
      body: formData
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Upload failed: ${response.status}`);
    }
    return response.json();
  },

  /**
   * Confirm bulk participants import on backend
   */
  /**
   * `autoGenerate` draws the fixtures and publishes the schedule as part of
   * the import. It was never sent at all, so the button offering it did the
   * import and stopped -- the organiser was told they had a schedule and had
   * none. It stays off unless asked for: the server will not redraw over
   * recorded results either way, but a draw is not a side effect of adding a
   * player to the list.
   */
  async confirmImport(tournamentId: string, players: any[], autoGenerate = false) {
    const { API_BASE_URL } = await import('../utils/apiClient');
    const formData = new FormData();
    formData.append('tournamentId', tournamentId);
    formData.append('players_json', JSON.stringify(players));
    formData.append('autoGenerate', autoGenerate ? 'true' : 'false');

    const token = localStorage.getItem('auth_token');
    const headers: HeadersInit = {};
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(`${API_BASE_URL}/imports/confirm`, {
      method: 'POST',
      headers,
      body: formData
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Confirmation failed: ${response.status}`);
    }
    return response.json();
  }
};

