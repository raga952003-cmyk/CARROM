/**
 * What to re-read when Supabase Realtime says something changed.
 *
 * Two decisions live here, and both used to be made inline inside the
 * provider's subscription callback, where nothing could test them.
 *
 * WHAT: every watched table names the reads it can have changed. Registrations,
 * matches and boards are not reads of their own — they arrive hydrated inside
 * the tournament payload — so scoring a board costs the draw and nothing else.
 * It used to re-read the player directory, the saved teams and the
 * notifications alongside it, on every board of every match.
 *
 * WHETHER: an admin's own write comes back over the websocket a moment after
 * the mutation has already re-read what it changed. Re-reading again is a
 * second round trip for an answer the client is holding, on every single
 * click. A read ISSUED after the last change in the window reached us
 * necessarily queried a database where all of those changes were committed, so
 * it saw them; anything with no such read still refreshes.
 */

/** The four independent reads behind the screen. */
export type Resource = 'tournaments' | 'players' | 'teams' | 'notifications';

export const ALL_RESOURCES: Resource[] = [
  'tournaments', 'players', 'teams', 'notifications',
];

/**
 * Which reads each watched table can have changed.
 *
 * `registrations` names three. Entering a tournament is not only an entry: a
 * doubles pair whose partner has no account yet creates that profile and the
 * team as part of the same write (see register_for_tournament), so the roster
 * and the saved pairs move with it. Nothing else brings those two back —
 * `profiles` and `teams` are not in the supabase_realtime publication
 * (migration 002 publishes five tables and neither is among them), so no event
 * ever names them. Until they are published, this and the pull on reconnect
 * are what keep the roster current on a screen that did not make the change.
 */
export const RESOURCE_FOR_TABLE: Record<string, Resource[]> = {
  tournaments: ['tournaments'],
  registrations: ['tournaments', 'teams', 'players'],
  matches: ['tournaments'],
  boards: ['tournaments'],
  notifications: ['notifications'],
};

/**
 * The resources a realtime window still requires a read of.
 *
 * `observedAt` is when the LAST change in the window reached us, and
 * `issuedAt` when each resource's most recent SUCCESSFUL read was issued. An
 * observedAt of 0 means the pull on reconnect, which reconciles everything:
 * the connection was down for an unknown stretch and no stamp can speak for
 * what happened during it.
 *
 * Being wrong in the safe direction costs a redundant read; being wrong in the
 * other direction leaves somebody else's change off the screen with no poll
 * coming to correct it, so every uncertain case refreshes.
 */
export function resourcesToRefresh(
  tables: string[],
  observedAt: number,
  issuedAt: Partial<Record<Resource, number>>,
): Resource[] {
  if (!observedAt) return [...ALL_RESOURCES];
  const touched = Array.from(new Set(
    tables.flatMap(t => RESOURCE_FOR_TABLE[t] || [])
  ));
  return touched.filter(r => (issuedAt[r] || 0) <= observedAt);
}
