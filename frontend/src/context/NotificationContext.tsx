import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, Info, X } from 'lucide-react';
import { NavigationAbortError, ApiError } from '../utils/apiClient';

/**
 * The app-wide place where a failure becomes something the user can read.
 *
 * Before this existed there were 92 click handlers shaped
 * `onClick={() => someAsyncCall(...)}`. A floating promise like that has no
 * rejection handler, so when the API said no the browser printed
 * "Uncaught (in promise) Error: ..." and the screen showed nothing at all. The
 * operator saw a button that did nothing and a console full of red.
 *
 * Two things fix that together: a visible surface (this), and a global listener
 * that catches what no local handler caught (installed below).
 */

type Level = 'error' | 'info' | 'success';

interface Note {
  id: number;
  level: Level;
  message: string;
}

interface NotificationApi {
  error: (message: string) => void;
  info: (message: string) => void;
  success: (message: string) => void;
  /** Report a caught exception. Returns the message shown, for callers that also want it inline. */
  report: (e: unknown, fallback?: string) => string;
  dismiss: (id: number) => void;
}

const NotificationContext = createContext<NotificationApi | null>(null);

/**
 * Pull something a human can act on out of whatever was thrown.
 *
 * The API puts its explanation in FastAPI's `detail`, which apiClient has
 * already unwrapped into Error.message, so most of the time that is exactly the
 * sentence to show.
 */
export function messageOf(e: unknown, fallback = 'Something went wrong. Please try again.'): string {
  if (typeof e === 'string' && e.trim()) return e;
  if (e instanceof ApiError) {
    // 5xx text is written for operators, not players, so it is replaced.
    if (e.status >= 500) return 'The server had a problem handling that. Please try again.';
    return e.message || fallback;
  }
  if (e instanceof Error && e.message) return e.message;
  const detail = (e as any)?.detail ?? (e as any)?.message;
  if (typeof detail === 'string' && detail.trim()) return detail;
  return fallback;
}

/**
 * True for failures that are not failures: a request cut short because the page
 * is being reloaded or navigated away from. Reporting those would mean a toast
 * every time the operator refreshes.
 */
function isNavigationNoise(e: unknown): boolean {
  if (e instanceof NavigationAbortError) return true;
  const name = (e as any)?.name;
  return name === 'NavigationAbortError' || name === 'AbortError';
}

let nextId = 1;

export const NotificationProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [notes, setNotes] = useState<Note[]>([]);
  const timers = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());

  const dismiss = useCallback((id: number) => {
    setNotes(prev => prev.filter(n => n.id !== id));
    const t = timers.current.get(id);
    if (t) { clearTimeout(t); timers.current.delete(id); }
  }, []);

  const push = useCallback((level: Level, message: string) => {
    const id = nextId++;
    setNotes(prev => {
      // The same failure repeated (an impatient double click) should read as one
      // problem, not a wall of identical toasts.
      if (prev.some(n => n.message === message && n.level === level)) return prev;
      // Keep the stack short enough to stay readable.
      return [...prev.slice(-3), { id, level, message }];
    });
    // Errors stay until dismissed; anything else clears itself.
    if (level !== 'error') {
      timers.current.set(id, setTimeout(() => dismiss(id), 4000));
    }
    return id;
  }, [dismiss]);

  const api = useMemo<NotificationApi>(() => ({
    error: (m: string) => { push('error', m); },
    info: (m: string) => { push('info', m); },
    success: (m: string) => { push('success', m); },
    report: (e: unknown, fallback?: string) => {
      const msg = messageOf(e, fallback);
      if (!isNavigationNoise(e)) push('error', msg);
      return msg;
    },
    dismiss,
  }), [push, dismiss]);

  useEffect(() => () => { timers.current.forEach(clearTimeout); timers.current.clear(); }, []);

  // The safety net. Every promise rejection that no local handler caught lands
  // here, which is why fixing this one place fixes all 92 call sites at once.
  useEffect(() => {
    const onRejection = (event: PromiseRejectionEvent) => {
      const reason = event.reason;
      if (isNavigationNoise(reason)) {
        // Still stop the console line: a reload is not an error to report.
        event.preventDefault();
        return;
      }
      // preventDefault is the part that actually removes
      // "Uncaught (in promise) ..." from the console. Showing the toast without
      // it would leave exactly the noise this is meant to clear.
      event.preventDefault();
      push('error', messageOf(reason));
      // Kept for debugging, but only while developing. In production the
      // operator already has the toast, and a console line per failure is the
      // very noise this exists to remove.
      if (import.meta.env.DEV) console.warn('[handled rejection]', reason);
    };

    const onError = (event: ErrorEvent) => {
      if (isNavigationNoise(event.error)) return;
      push('error', messageOf(event.error, event.message));
    };

    window.addEventListener('unhandledrejection', onRejection);
    window.addEventListener('error', onError);
    return () => {
      window.removeEventListener('unhandledrejection', onRejection);
      window.removeEventListener('error', onError);
    };
  }, [push]);

  return (
    <NotificationContext.Provider value={api}>
      {children}
      <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 w-[min(24rem,calc(100vw-2rem))]">
        {notes.map(n => (
          <div
            key={n.id}
            role={n.level === 'error' ? 'alert' : 'status'}
            className={`flex items-start gap-2.5 px-4 py-3 rounded-xl shadow-lg border text-xs font-medium animate-in slide-in-from-bottom-2 ${
              n.level === 'error'
                ? 'bg-red-50 border-red-300 text-red-900'
                : n.level === 'success'
                  ? 'bg-emerald-50 border-emerald-300 text-emerald-900'
                  : 'bg-white border-gray-300 text-gray-800'
            }`}
          >
            {n.level === 'error'
              ? <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0 text-red-600" />
              : n.level === 'success'
                ? <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0 text-emerald-600" />
                : <Info className="w-4 h-4 mt-0.5 shrink-0 text-gray-500" />}
            <span className="flex-1 leading-snug">{n.message}</span>
            <button
              onClick={() => dismiss(n.id)}
              aria-label="Dismiss"
              className="p-0.5 rounded hover:bg-black/5 shrink-0"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        ))}
      </div>
    </NotificationContext.Provider>
  );
};

/**
 * Deliberately safe to call outside the provider: it falls back to the console
 * rather than throwing. A missing provider must never be the reason a screen
 * crashes while it is trying to report a different problem.
 */
export function useNotify(): NotificationApi {
  const ctx = useContext(NotificationContext);
  const fallback = useMemo<NotificationApi>(() => ({
    error: (m: string) => console.error(m),
    info: (m: string) => console.info(m),
    success: (m: string) => console.info(m),
    report: (e: unknown, f?: string) => { const m = messageOf(e, f); console.error(m); return m; },
    dismiss: () => {},
  }), []);
  return ctx ?? fallback;
}
