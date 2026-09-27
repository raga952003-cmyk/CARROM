import { Match, Tournament } from '../types/tournament';
import { compareMatches } from './matchOrder';

/**
 * Personal fixtures are matched by account id or a team containing that id.
 * Imported roster entries need explicit account linking; a matching name
 * cannot prove ownership of a fixture.
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

  return [...byId].sort(compareMatches);
}

/** Who the other side is, given one of this person's matches. */
export function opponentOf(
  match: Match,
  user: { id?: string; name?: string } | null | undefined,
  tournament?: Tournament | null,
): string {
  const teamIds = new Set((tournament?.registrations || [])
    .filter(r => r.type === 'doubles' && r.team &&
      (r.team.player1?.id === user?.id || r.team.player2?.id === user?.id))
    .map(r => r.team!.id));
  if (!user?.id) return 'TBD';
  if (match.player1Id === user.id || teamIds.has(match.player1Id)) {
    return match.player2Name || 'TBD';
  }
  if (match.player2Id === user.id || teamIds.has(match.player2Id)) {
    return match.player1Name || 'TBD';
  }
  return 'TBD';
}
