import { Match, Tournament } from '../types/tournament';
import { compareMatches } from './matchOrder';

/**
 * The fixtures belonging to one person.
 *
 * Identity is the profile id, and a doubles fixture carries the TEAM id, so the
 * teams someone belongs to count as them. That much is straightforward.
 *
 * The awkward part is that a signed-in account and a roster entry are not
 * always the same row. Players imported from a sheet get their own profile; if
 * someone then signs in with an account that has no profile row — or a second
 * one — /auth/me falls back to their auth metadata and hands back an id that
 * appears in no fixture. The person is in the draw nineteen times and their
 * screen says "No personal matches found".
 *
 * So the id is tried first and, only when it matches nothing at all, the exact
 * name is used as a fallback. Exact, not a substring: the substring rule this
 * replaced matched "Srinivas" against "Srinivasan S" and showed people other
 * people's fixtures.
 */
export function findMyMatches(
  tournament: Tournament | undefined | null,
  user: { id?: string; name?: string } | null | undefined,
): Match[] {
  const matches = tournament?.matches || [];
  if (!matches.length || !user) return [];

  const teamIds = new Set(
    (tournament?.registrations || [])
      .filter(r => r.type === 'doubles' && r.team &&
        (r.team.player1?.id === user.id || r.team.player2?.id === user.id))
      .map(r => r.team!.id)
  );

  const byId = user.id
    ? matches.filter(m =>
        m.player1Id === user.id || m.player2Id === user.id ||
        teamIds.has(m.player1Id) || teamIds.has(m.player2Id))
    : [];

  if (byId.length) return [...byId].sort(compareMatches);

  const name = (user.name || '').trim().toLowerCase();
  if (!name) return [];
  const byName = matches.filter(m =>
    (m.player1Name || '').trim().toLowerCase() === name ||
    (m.player2Name || '').trim().toLowerCase() === name);

  return [...byName].sort(compareMatches);
}

/** Who the other side is, given one of this person's matches. */
export function opponentOf(
  match: Match,
  user: { id?: string; name?: string } | null | undefined,
): string {
  const mine = match.player1Id === user?.id
    || (match.player1Name || '').trim().toLowerCase()
       === (user?.name || '').trim().toLowerCase();
  return (mine ? match.player2Name : match.player1Name) || 'TBD';
}
