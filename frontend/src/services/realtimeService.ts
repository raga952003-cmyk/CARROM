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

export type RealtimeStatus = 'connecting' | 'live' | 'polling' | 'disabled';

export interface RealtimeHandle {
  unsubscribe: () => void;
}

interface SubscribeOptions {
  /** Called (debounced) whenever any watched table changes. */
  onChange: () => void;
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

  // Several rows change per score submission; coalesce them into one refresh.
  const scheduleRefresh = () => {
    if (closed) return;
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      debounceTimer = null;
      onChange();
    }, debounceMs);
  };

  onStatus?.('connecting');

  const channel = supabase.channel('carrom-tournament-stream');

  for (const table of WATCHED_TABLES) {
    channel.on(
      'postgres_changes',
      { event: '*', schema: 'public', table },
      scheduleRefresh
    );
  }

  channel.subscribe((status: string) => {
    if (closed) return;
    if (status === 'SUBSCRIBED') {
      onStatus?.('live');
      // Pull once on connect so the view is current from the first frame.
      onChange();
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
