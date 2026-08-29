/**
 * Access Service
 * What the signed-in admin may do on a given tournament.
 *
 * The backend has always exposed this (GET /access/tournaments/{id}/me, whose
 * docstring says it "drives which controls the UI offers") but nothing ever
 * called it. So the dashboard offered Delete, Publish and Generate on every
 * tournament regardless of who owned it, and the only way to discover the
 * answer was to press the button and collect a 403.
 *
 * That matters beyond the wasted round trip: a 4xx is written to the browser
 * console by the network layer itself, before any JavaScript sees it. No error
 * handler can suppress that line. The only way not to have it is not to send a
 * request that was always going to fail.
 */

import { apiClient } from '../utils/apiClient';

export interface TournamentAccess {
  isOwner: boolean;
  role: string | null;
  canManage: boolean;
  canScore: boolean;
  enforced: boolean;
  status: string | null;
}

/** Assumed when the check itself fails, so a lookup problem cannot lock an admin out of their own screen. */
export const PERMISSIVE_ACCESS: TournamentAccess = {
  isOwner: false, role: null, canManage: true, canScore: true, enforced: false, status: 'unknown',
};

export const accessService = {
  async myAccessFor(tournamentId: string) {
    return apiClient.get<TournamentAccess>(`/access/tournaments/${tournamentId}/me`);
  },
};
