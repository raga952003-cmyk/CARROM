/**
 * Supabase Realtime subscriptions (spec 72, 73).
 *
 * Replaces the previous 5-second poll loop. Spec 91 is explicit: do not
 * continuously poll the database for live tournament information when Realtime
 * subscriptions can be used.
 *
 * Reads flow straight from Postgres replication to authorised clients; writes
 * still go through the serverless API, so the backend remains the only thing
 * that can change official data.
 */

import { supabase, isSupabaseConfigured } from '../utils/supabaseClient';

/** Tables whose changes should refresh the tournament view. */
const WATCHED_TABLES = [
  'tournaments',
  'registrations',
  'matches',
  'boards',
  'notifications',
] as const;

export type WatchedTable = (typeof WATCHED_TABLES)[number];

export type RealtimeStatus = 'connecting' | 'live' | 'polling' | 'disabled';

export interface RealtimeHandle {
  unsubscribe: () => void;
}

/** What the debounce window saw, so the caller can re-read only what moved. */
export interface RealtimeChange {
  /** The watched tables that changed, deduplicated. */
  tables: WatchedTable[];
  /**
   * When the LAST change in this window reached us (Date.now()).
   *
   * The caller uses this to recognise the echo of its own write: a read it
   * issued after this instant already queried a database where every change in
   * the window was committed, so re-reading would spend a round trip to learn
   * what it holds.
   *
   * The last and not the first, which is the whole point. A window can hold
   * our own write AND somebody else's arriving just after it: stamped by the
   * first, our own follow-up read would look like it covered both, and the
   * other person's change would be dropped. On a live connection there is no
   * poll to pick it up again, so it would sit there stale until something
   * unrelated happened to change.
   *
   * Receipt time, not the database's commit timestamp — the two clocks are not
   * the same one, and the cost of being wrong this way is a redundant read
   * rather than a stale screen.
   */
  observedAt: number;
}

interface SubscribeOptions {
  /** Called (debounced) whenever any watched table changes. */
  onChange: (change: RealtimeChange) => void;
  /** Called when the connection state changes, so the UI can show live vs fallback. */
  onStatus?: (status: RealtimeStatus) => void;
  /** Debounce window in ms — a score submit touches several tables at once. */
  debounceMs?: number;
}

/**
 * Subscribe to tournament data changes.
 *
 * Returns a handle whose `unsubscribe` must be called on unmount; leaking
 * channels keeps websockets open and costs money (spec 91).
 */
export function subscribeToTournamentData({
  onChange,
  onStatus,
  debounceMs = 250,
}: SubscribeOptions): RealtimeHandle {
  if (!isSupabaseConfigured || !supabase) {
    onStatus?.('disabled');
    return { unsubscribe: () => {} };
  }

  let debounceTimer: ReturnType<typeof setTimeout> | null = null;
  let closed = false;
  // What the current debounce window has seen. Which tables moved decides what
  // the caller re-reads: a board score must not cost a re-read of the player
  // directory as well.
  let pendingTables = new Set<WatchedTable>();
  let pendingLatest = 0;

  // Several rows change per score submission; coalesce them into one refresh.
  const scheduleRefresh = (table: WatchedTable) => {
    if (closed) return;
    pendingTables.add(table);
    // Stamped by the LATEST change in the window. Only a read issued after
    // that instant can be said to have seen everything in it.
    pendingLatest = Date.now();
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      debounceTimer = null;
      const tables = Array.from(pendingTables);
      const observedAt = pendingLatest;
      pendingTables = new Set();
      pendingLatest = 0;
      onChange({ tables, observedAt });
    }, debounceMs);
  };

  onStatus?.('connecting');

  const channel = supabase.channel('carrom-tournament-stream');

  for (const table of WATCHED_TABLES) {
    channel.on(
      'postgres_changes',
      { event: '*', schema: 'public', table },
      () => scheduleRefresh(table)
    );
  }

  channel.subscribe((status: string) => {
    if (closed) return;
    if (status === 'SUBSCRIBED') {
      onStatus?.('live');
      // Pull everything once on connect so the view is current from the first
      // frame. observedAt 0 marks it as unconditional: nothing already read
      // can be newer than a connection that has just opened.
      onChange({ tables: [...WATCHED_TABLES], observedAt: 0 });
    } else if (
      status === 'CHANNEL_ERROR' ||
      status === 'TIMED_OUT' ||
      status === 'CLOSED'
    ) {
      // Realtime is unavailable (network, or the tables are not in the
      // supabase_realtime publication). The caller falls back to slow polling.
      onStatus?.('polling');
    }
  });

  return {
    unsubscribe: () => {
      closed = true;
      if (debounceTimer) clearTimeout(debounceTimer);
      try {
        supabase.removeChannel(channel);
      } catch {
        /* channel already torn down */
      }
    },
  };
}
