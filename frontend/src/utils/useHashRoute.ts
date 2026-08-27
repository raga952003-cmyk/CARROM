import { useEffect, useState } from 'react';

/**
 * Minimal hash router.
 *
 * The app was a single state-driven screen with no routes, but board mode,
 * the spectator view and the print sheets all need to be reachable by URL --
 * a scorer opens their board on a phone, a print sheet opens in a new tab.
 * Hash routing gives that without adding a routing dependency or needing any
 * server-side rewrite rules.
 *
 *   #/board/3?t=<tournamentId>   one board's running order and scoring
 *   #/live/<tournamentId>        public spectator view, no sign-in
 *   #/print/<kind>/<tournamentId>  printable sheet
 */
export interface HashRoute {
  /** '' for the normal app, otherwise the first path segment. */
  view: string;
  segments: string[];
  params: URLSearchParams;
  raw: string;
}

function parse(hash: string): HashRoute {
  const cleaned = hash.replace(/^#\/?/, '');
  const [path, query = ''] = cleaned.split('?');
  const segments = path.split('/').filter(Boolean).map(decodeURIComponent);
  return {
    view: segments[0] || '',
    segments,
    params: new URLSearchParams(query),
    raw: hash,
  };
}

export function useHashRoute(): HashRoute {
  const [route, setRoute] = useState<HashRoute>(() => parse(window.location.hash));

  useEffect(() => {
    const onChange = () => setRoute(parse(window.location.hash));
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);

  return route;
}

export function navigateTo(path: string) {
  window.location.hash = path.startsWith('#') ? path : `#/${path.replace(/^\//, '')}`;
}

export function exitToApp() {
  window.location.hash = '';
}
