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

export function publicPosterUrl(id: string): string {
  return `${window.location.origin}${window.location.pathname}#/poster/${encodeURIComponent(id)}`;
}
