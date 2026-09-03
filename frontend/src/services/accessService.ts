/**
 * Access Service
 * Who may run a tournament, and how someone else gets let in.
 *
 * Whoever creates a tournament owns it. Another admin sees it but cannot touch
 * it until the owner lets them in — either because they asked and were
 * approved, or because the owner handed them access directly.
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

export type AccessRole = 'manager' | 'scorer';
export type AccessStatus = 'pending' | 'approved' | 'rejected' | 'revoked' | 'owner' | 'unenforced' | null;

export interface TournamentAccess {
  isOwner: boolean;
  role: string | null;
  canManage: boolean;
  canScore: boolean;
  enforced: boolean;
  status: AccessStatus;
}

/**
 * One row of the access table, as the owner and the requester both see it.
 *
 * The API hydrates each row with the requester's profile and the tournament
 * under their own keys rather than flattening them, so read names through
 * `requester`, not off the row.
 */
export interface AccessRequest {
  id: string;
  tournamentId: string;
  userId: string;
  requester?: { id: string; name?: string; email?: string; club?: string; role?: string } | null;
  tournament?: { id: string; name?: string; ownerId?: string } | null;
  accessRole: AccessRole;
  status: AccessStatus;
  message?: string | null;
  decisionNote?: string | null;
  requestedAt?: string;
  decidedAt?: string | null;
}

export interface GrantableAdmin {
  id: string;
  name: string;
  email: string;
}

/**
 * Assumed when the check itself fails.
 *
 * Permissive on purpose: a lookup that failed says nothing about what the
 * caller may do, and locking an organiser out of their own tournament because
 * one request timed out is the worse of the two mistakes. The server is still
 * the one that decides -- every one of these controls is checked again there,
 * and refused with a message naming the owner -- so the cost of being wrong
 * this way is a button that answers 403, not an action that should not have
 * happened.
 *
 * `status` is 'unknown' rather than a real status, so a screen can tell "we
 * could not find out" from "you are not allowed".
 */
export const PERMISSIVE_ACCESS: TournamentAccess = {
  isOwner: false, role: null, canManage: true, canScore: true, enforced: false, status: 'unknown' as AccessStatus,
};

/** What an admin who is definitively not let in may do here: nothing. */
export const NO_ACCESS: TournamentAccess = {
  isOwner: false, role: null, canManage: false, canScore: false, enforced: true, status: null,
};

export const accessService = {
  /** What the signed-in admin may do here. */
  async myAccessFor(tournamentId: string) {
    return apiClient.get<TournamentAccess>(`/access/tournaments/${tournamentId}/me`);
  },

  /** Ask the owner to be let in. */
  async requestAccess(tournamentId: string, role: AccessRole, message?: string) {
    return apiClient.post<AccessRequest>(`/access/tournaments/${tournamentId}/request`, { role, message });
  },

  /** Every request on one tournament — owner only. */
  async listRequests(tournamentId: string) {
    return apiClient.get<AccessRequest[]>(`/access/tournaments/${tournamentId}/requests`);
  },

  /** Everything awaiting this owner's decision, across their tournaments. */
  async listPending() {
    return apiClient.get<AccessRequest[]>('/access/pending');
  },

  /** This admin's own access records, including anything still pending. */
  async listMine() {
    return apiClient.get<{ owned: any[]; requests: AccessRequest[]; enforced: boolean }>('/access/mine');
  },

  async approve(requestId: string, role?: AccessRole, note?: string) {
    return apiClient.post<AccessRequest>(`/access/requests/${requestId}/approve`, { role, note });
  },

  async reject(requestId: string, note?: string) {
    return apiClient.post<AccessRequest>(`/access/requests/${requestId}/reject`, { note });
  },

  async revoke(requestId: string, note?: string) {
    return apiClient.post<AccessRequest>(`/access/requests/${requestId}/revoke`, { note });
  },

  /** Hand access to someone who never asked. */
  async grant(tournamentId: string, target: { userId?: string; email?: string }, role: AccessRole, note?: string) {
    return apiClient.post<AccessRequest>(`/access/tournaments/${tournamentId}/grant`, {
      userId: target.userId, email: target.email, role, note,
    });
  },

  /** Admin accounts the owner can pick from when granting. */
  async grantableAdmins() {
    return apiClient.get<GrantableAdmin[]>('/access/admins');
  },
};
