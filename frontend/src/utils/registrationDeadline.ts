/** Registration dates are calendar dates in the tournament's India timezone. */
export const isRegistrationDeadlinePassed = (
  registrationEndDate: string,
  now: Date = new Date(),
): boolean => {
  const deadline = registrationEndDate.slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(deadline)) return true;

  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Kolkata',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now);
  const dateParts = Object.fromEntries(parts.map(part => [part.type, part.value]));
  const today = `${dateParts.year}-${dateParts.month}-${dateParts.day}`;
  return today > deadline;
};
