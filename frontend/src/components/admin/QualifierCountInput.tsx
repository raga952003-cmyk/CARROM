import React, { useEffect, useState } from 'react';

/** Keep the field editable while blank; commit only valid whole counts. */
export const QualifierCountInput: React.FC<{
  value: number;
  onChange: (count: number) => void;
  min?: number;
  max?: number;
  label: string;
}> = ({ value, onChange, min = 2, max = 1024, label }) => {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  return <input
    type="number" inputMode="numeric" step={1} min={min} max={max}
    aria-label={label} value={text}
    onChange={e => {
      setText(e.target.value);
      const count = Number(e.target.value);
      if (Number.isInteger(count) && count >= min && count <= max) onChange(count);
    }}
    onBlur={() => setText(String(value))}
    className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
  />;
};

/** The same seeded bye structure used by the server's knockout engine. */
export function knockoutProgression(count: number): string {
  if (count < 2) return 'Too few entrants for a knockout.';
  let slots = 2;
  while (slots < count) slots *= 2;
  const rounds: string[] = [];
  if (slots > count) {
    rounds.push(`Preliminary: ${count - slots / 2} matches; top ${slots - count} seeds receive a bye`);
    slots /= 2;
  }
  while (slots >= 2) {
    rounds.push(slots === 8 ? 'Quarterfinals (8)' : slots === 4 ? 'Semifinals (4)' : slots === 2 ? 'Final (2)' : `Round of ${slots}`);
    slots /= 2;
  }
  return rounds.join(' → ');
}
