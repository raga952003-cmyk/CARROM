export type RefreshScope = string[] | null;

/** Serialize reads so an older response cannot replace a newer saved score.
 * Callers arriving during a read share one follow-up that includes every scope.
 * null means a full read and always takes precedence over individual IDs.
 */
export function createRefreshQueue<K extends string>(
  read: (key: K, scope: RefreshScope) => Promise<void>,
) {
  const running = new Map<K, Promise<void>>();
  const queued = new Map<K, { scope: RefreshScope; promise: Promise<void> }>();
  const run = (key: K, scope: RefreshScope = null): Promise<void> => {
    const current = running.get(key);
    if (current) {
      const next = queued.get(key);
      if (next) {
        next.scope = next.scope === null || scope === null
          ? null : [...new Set([...next.scope, ...scope])];
        return next.promise;
      }
      const follow = { scope, promise: Promise.resolve() };
      follow.promise = current.catch(() => undefined).then(() => {
        queued.delete(key);
        return run(key, follow.scope);
      });
      queued.set(key, follow);
      return follow.promise;
    }
    const request = read(key, scope).finally(() => { running.delete(key); });
    running.set(key, request);
    return request;
  };
  return run;
}
