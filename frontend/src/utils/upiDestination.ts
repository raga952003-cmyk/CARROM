/** Match the backend's accepted tournament UPI ID or Indian GPay number. */
export function validTournamentUpiDestination(value: string | null | undefined): string | null {
  const destination = typeof value === 'string' ? value.trim() : '';
  return /^[6-9][0-9]{9}$/.test(destination) ||
    /^[A-Za-z0-9._-]{2,100}@[A-Za-z0-9.-]{2,100}$/.test(destination)
    ? destination
    : null;
}
