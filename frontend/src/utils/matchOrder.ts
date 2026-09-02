/**
 * Putting matches in the order they will actually be played.
 *
 * `scheduledTime` is stored as a display string — "9:35 AM", "2:50 PM" — and
 * five different screens sorted it with `localeCompare`. Alphabetically
 * "2:50 PM" comes before "9:15 PM" comes before "9:35 AM", so every list that
 * spanned a morning and an afternoon was in the wrong order: the umpire's
 * queue on the phone, the player's "next match", the public board, the printed
 * board sheets. Nothing looked broken — the times were all correct, just
 * arranged wrongly — which is why it survived this long.
 */

interface Orderable {
  scheduledDate?: string | null;
  scheduledTime?: string | null;
  matchNumber?: number;
}

/**
 * Minutes since midnight, or null when the time is missing or unparseable.
 *
 * Accepts "9:35 AM", "09:35", "9:35 pm" and "21:35". An unrecognised value
 * returns null rather than 0, so a broken time sorts to the end instead of
 * pretending to be midnight and jumping to the front of the queue.
 */
export function minutesOfDay(value?: string | null): number | null {
  const text = (value || '').trim();
  if (!text) return null;

  const m = text.match(/^(\d{1,2}):(\d{2})\s*([AaPp][Mm])?/);
  if (!m) return null;

  let hours = parseInt(m[1], 10);
  const mins = parseInt(m[2], 10);
  if (Number.isNaN(hours) || Number.isNaN(mins) || mins > 59) return null;

  const meridiem = (m[3] || '').toLowerCase();
  if (meridiem === 'pm' && hours !== 12) hours += 12;
  if (meridiem === 'am' && hours === 12) hours = 0;
  if (hours > 23) return null;

  return hours * 60 + mins;
}

/**
 * Chronological order: date, then time, then match number as the tie-break.
 *
 * Matches with no date or time sort after those that have one — an unscheduled
 * fixture is not "first thing in the morning".
 */
export function compareMatches(a: Orderable, b: Orderable): number {
  const dateA = a.scheduledDate || '';
  const dateB = b.scheduledDate || '';
  if (dateA !== dateB) {
    if (!dateA) return 1;
    if (!dateB) return -1;
    return dateA.localeCompare(dateB);
  }

  const timeA = minutesOfDay(a.scheduledTime);
  const timeB = minutesOfDay(b.scheduledTime);
  if (timeA !== timeB) {
    if (timeA === null) return 1;
    if (timeB === null) return -1;
    return timeA - timeB;
  }

  return (a.matchNumber || 0) - (b.matchNumber || 0);
}
