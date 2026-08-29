/**
 * API Client for Python Backend
 * Base configuration and HTTP methods
 */

const env = (import.meta as any).env || {};
// Same-origin '/api' anywhere but a local dev machine. The built bundle used
// to hardcode localhost:8000, so a deployed frontend called the developer's
// own machine and every request failed.
const isLocalHost = typeof window !== 'undefined' &&
  /^(localhost|127\.0\.0\.1|\[::1\])$/.test(window.location.hostname);
const API_BASE_URL = env.VITE_API_URL || (isLocalHost ? 'http://localhost:8000/api' : '/api');

const ACCESS_KEY = 'auth_token';
const REFRESH_KEY = 'auth_refresh_token';
const EXPIRY_KEY = 'auth_expires_at';

/**
 * Raised on the window when the session cannot be recovered.
 *
 * The client cannot navigate on its own, so it announces the failure and the
 * app signs the user out. Without this the token was silently cleared while
 * the app still believed it was signed in, and the refresh loop then retried
 * forever against an empty token.
 */
/**
 * A request killed because the page is going away is not a failure worth
 * reporting. Navigating or reloading rejects every in-flight fetch with a
 * TypeError, which otherwise reached the user as "your change was not saved"
 * and filled the console on every single page transition.
 */
let pageIsUnloading = false;
if (typeof window !== 'undefined') {
  const markUnloading = () => { pageIsUnloading = true; };
  window.addEventListener('pagehide', markUnloading);
  window.addEventListener('beforeunload', markUnloading);
}

/** Thrown when the page tore the request down; callers should stay quiet. */
export class NavigationAbortError extends Error {
  readonly isNavigationAbort = true;
  constructor() {
    super('Request cancelled because the page was navigating away.');
    this.name = 'NavigationAbortError';
  }
}

export const AUTH_EXPIRED_EVENT = 'carrom:auth-expired';

class ApiClient {
  private baseURL: string;
  private token: string | null = null;
  private refreshToken: string | null = null;
  /** In-flight refresh, so concurrent 401s trigger only one renewal. */
  private refreshInFlight: Promise<boolean> | null = null;

  constructor(baseURL: string) {
    this.baseURL = baseURL;
    this.loadToken();
  }

  private loadToken() {
    this.token = localStorage.getItem(ACCESS_KEY);
    this.refreshToken = localStorage.getItem(REFRESH_KEY);
  }

  setToken(token: string) {
    this.token = token;
    localStorage.setItem(ACCESS_KEY, token);
  }

  /** Store the whole session so the access token can be renewed later. */
  setSession(session: {
    access_token: string;
    refresh_token?: string | null;
    expires_at?: number | null;
  }) {
    this.setToken(session.access_token);
    if (session.refresh_token) {
      this.refreshToken = session.refresh_token;
      localStorage.setItem(REFRESH_KEY, session.refresh_token);
    }
    if (session.expires_at) {
      localStorage.setItem(EXPIRY_KEY, String(session.expires_at));
    }
  }

  clearToken() {
    this.token = null;
    this.refreshToken = null;
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
    localStorage.removeItem(EXPIRY_KEY);
  }

  hasSession(): boolean {
    return !!localStorage.getItem(ACCESS_KEY);
  }

  /** Seconds until the access token expires; null when unknown. */
  secondsUntilExpiry(): number | null {
    const raw = localStorage.getItem(EXPIRY_KEY);
    if (!raw) return null;
    const expiresAt = Number(raw);
    if (!Number.isFinite(expiresAt)) return null;
    return expiresAt - Math.floor(Date.now() / 1000);
  }

  /**
   * Renew the access token. Returns false when the session is unrecoverable,
   * in which case the caller must sign the user out.
   */
  async refreshSession(): Promise<boolean> {
    if (this.refreshInFlight) return this.refreshInFlight;

    const refreshToken = this.refreshToken || localStorage.getItem(REFRESH_KEY);
    if (!refreshToken) return false;

    this.refreshInFlight = (async () => {
      try {
        const response = await fetch(`${this.baseURL}/auth/refresh`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: refreshToken }),
        });
        if (!response.ok) return false;
        const session = await response.json();
        if (!session?.access_token) return false;
        this.setSession(session);
        return true;
      } catch {
        return false;
      } finally {
        this.refreshInFlight = null;
      }
    })();

    return this.refreshInFlight;
  }

  private endSession(detail: string) {
    this.clearToken();
    window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT, { detail }));
  }

  private getHeaders(): HeadersInit {
    const headers: HeadersInit = {
      'Content-Type': 'application/json',
    };

    if (this.token) {
      headers['Authorization'] = `Bearer ${this.token}`;
    }

    return headers;
  }

  async request<T>(
    endpoint: string,
    method: string = 'GET',
    data?: any,
    isRetry: boolean = false
  ): Promise<T> {
    const url = `${this.baseURL}${endpoint}`;

    const options: RequestInit = {
      method,
      headers: this.getHeaders(),
    };

    if (data && (method === 'POST' || method === 'PUT' || method === 'PATCH')) {
      options.body = JSON.stringify(data);
    }

    try {
      const response = await fetch(url, options);

      if (!response.ok) {
        // Read the body once, before branching: the API puts the useful
        // explanation in `detail` (e.g. which role the account actually has).
        const errorData = await response.json().catch(() => ({} as any));
        const detail = typeof errorData?.detail === 'string' ? errorData.detail : '';

        if (response.status === 401) {
          // One renewal attempt, then replay the original request. Only give
          // up -- and sign out -- when the refresh itself fails.
          if (!isRetry && endpoint !== '/auth/refresh' && this.refreshToken) {
            const renewed = await this.refreshSession();
            if (renewed) {
              return this.request<T>(endpoint, method, data, true);
            }
          }
          this.endSession(detail || 'Session expired. Please sign in again.');
          throw new Error(detail || 'Session expired. Please sign in again.');
        }
        if (response.status === 403) {
          throw new Error(detail || 'Access denied. Insufficient permissions.');
        }
        if (response.status === 404) {
          throw new Error(detail || 'Resource not found.');
        }
        throw new Error(detail || `HTTP Error: ${response.status}`);
      }

      // Handle 204 No Content
      if (response.status === 204) {
        return null as T;
      }

      return await response.json();
    } catch (error) {
      // fetch() rejects with a TypeError when the request never reached the
      // server. That is an Error, so the friendly fallback below was
      // unreachable and the user saw a bare "Failed to fetch" instead of being
      // told their change was not saved.
      if (error instanceof TypeError || !navigator.onLine) {
        if (pageIsUnloading) {
          throw new NavigationAbortError();
        }
        throw new Error(
          navigator.onLine
            ? 'Could not reach the server. Your change was not saved — try again.'
            : 'You appear to be offline. Your change was not saved — reconnect and try again.'
        );
      }
      if (error instanceof Error) {
        throw error;
      }
      throw new Error('Network error. Please check your connection.');
    }
  }

  // Convenience methods
  get<T>(endpoint: string): Promise<T> {
    return this.request<T>(endpoint, 'GET');
  }

  post<T>(endpoint: string, data: any): Promise<T> {
    return this.request<T>(endpoint, 'POST', data);
  }

  put<T>(endpoint: string, data: any): Promise<T> {
    return this.request<T>(endpoint, 'PUT', data);
  }

  patch<T>(endpoint: string, data: any): Promise<T> {
    return this.request<T>(endpoint, 'PATCH', data);
  }

  delete<T>(endpoint: string): Promise<T> {
    return this.request<T>(endpoint, 'DELETE');
  }
}

// Export singleton instance
export const apiClient = new ApiClient(API_BASE_URL);

// Export for testing/debugging
export { API_BASE_URL };
