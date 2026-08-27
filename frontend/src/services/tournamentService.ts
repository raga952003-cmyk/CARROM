/**
 * Tournament Service
 * Handles all tournament-related API calls
 */

import { apiClient } from '../utils/apiClient';
import { Tournament, StandingsBreakdown } from '../types/tournament';

export const tournamentService = {
  /**
   * Get all tournaments
   */
  async getAllTournaments() {
    return apiClient.get<Tournament[]>('/tournaments');
  },

  /**
   * Get tournament by ID
   */
  async getTournamentById(id: string) {
    return apiClient.get<Tournament>(`/tournaments/${id}`);
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
  async confirmImport(tournamentId: string, players: any[]) {
    const { API_BASE_URL } = await import('../utils/apiClient');
    const formData = new FormData();
    formData.append('tournamentId', tournamentId);
    formData.append('players_json', JSON.stringify(players));

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

