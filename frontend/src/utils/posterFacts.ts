import { PosterConfig, Tournament } from '../types/tournament';

export function scoringSummary(t: Tournament): string {
  const r = t.rules;
  if (r.scoringMode === 'remaining_coins' && r.boardEntryMode !== 'detailed')
    return `Simple scoring · winner scores opponent’s remaining coins · ${r.boardsPerSet || r.maxBoardsPerMatch} boards`;
  if (r.scoringMode === 'official_icf') return `Federation scoring · best of ${r.numberOfSets || 3} games · ${r.targetScore} points / ${r.boardsPerSet || r.maxBoardsPerMatch} boards`;
  return r.scoringMode === 'remaining_coins' ? 'Remaining-coins scoring · queen and penalties recorded' : 'Pocketed-coins scoring · tournament rules apply';
}

export function posterDefaults(t: Tournament): PosterConfig {
  return { themeStyle: 'emerald_gold', tagline: 'Play with focus. Compete with confidence.',
    badgeText: 'CARROM TOURNAMENT', announcement: '',
    posterSize: 'portrait', ...t.posterConfig, highlights: Array.isArray(t.posterConfig?.highlights) ? t.posterConfig.highlights.filter(h => typeof h === 'string') : [] };
}

export function posterFingerprint(t: Tournament): string {
  return JSON.stringify([t.name, t.venue, t.city, t.category, t.format, t.entryFee,
    t.prizePool, t.registrationStartDate, t.registrationEndDate,
    t.tournamentStartDate, t.tournamentEndDate, t.rules]);
}

export function posterDate(value: string): string {
  const date = new Date(`${value?.slice(0, 10)}T12:00:00`);
  return Number.isNaN(date.getTime()) ? 'To be announced' : date.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

export const DEFAULT_PUBLIC_SITE_URL = 'https://carrom-umber-six.vercel.app/';

function isLocalOrReservedHost(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/\.$/, '');
  // IPv6 addresses are deliberately excluded from shareable poster settings.
  if (host.includes(':') || host.startsWith('[')) return true;
  if (!host.includes('.') || /(^|\.)(localhost|local|test|invalid|internal|lan)$/.test(host)) return true;
  if (!/^\d+\.\d+\.\d+\.\d+$/.test(host)) return false;
  const [a, b, c] = host.split('.').map(Number);
  return a === 0 || a === 10 || a === 127 || a >= 224 ||
    (a === 100 && b >= 64 && b <= 127) ||
    (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && (b === 168 || (b === 0 && (c === 0 || c === 2)) || (b === 88 && c === 99))) ||
    (a === 198 && (b === 18 || b === 19 || (b === 51 && c === 100))) ||
    (a === 203 && b === 0 && c === 113);
}

/** A public website base, never a payment address or a local development URL. */
export function validatePosterBaseUrl(raw: string): string {
  const value = raw.trim();
  let url: URL;
  try { url = new URL(value); } catch { throw new Error('Enter the full public website address, starting with https://.'); }
  if (url.protocol !== 'https:') throw new Error('The poster website must use https://.');
  if (url.username || url.password) throw new Error('Use a website address without login details.');
  if (value.includes('?') || value.includes('#')) throw new Error('Use the website base address without a query or # section.');
  if (isLocalOrReservedHost(url.hostname)) throw new Error('Use a public website address. Local or private addresses cannot be shared with players.');
  return `${url.origin}${url.pathname.replace(/\/+$/, '')}/`;
}

export function isLocalPosterUrl(value: string): boolean {
  try { return isLocalOrReservedHost(new URL(value).hostname); } catch { return true; }
}

export function publicPosterUrl(id: string, configuredBaseUrl?: string): string {
  const env = (import.meta as any).env || {};
  const base = configuredBaseUrl?.trim() || String(env.VITE_PUBLIC_SITE_URL || '').trim() || DEFAULT_PUBLIC_SITE_URL;
  return `${validatePosterBaseUrl(base)}#/poster/${encodeURIComponent(id)}`;
}
