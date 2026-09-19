import React from 'react';
import { Layers, Users, Info } from 'lucide-react';

interface GroupStageSettingsProps {
  format: string;
  groupCount: number;
  qualifiersPerGroup: number;
  /**
   * How many league finishers reach the knockout when there is ONE league.
   * The "Who advances" control below sets it; with groups, qualifiersPerGroup
   * does instead. One decision, one place.
   */
  knockoutQualifiers: number;
  onKnockoutQualifiersChange: (n: number) => void;
  expectedEntrants: number;
  onGroupCountChange: (n: number) => void;
  onQualifiersChange: (n: number) => void;
  onExpectedEntrantsChange: (n: number) => void;
}

/** What a bracket of this size opens with, so the number means something. */
const ROUND_NAME: Record<number, string> = {
  2: 'Final only',
  4: 'Semi Finals',
  8: 'Quarter Finals',
  16: 'Round of 16',
  32: 'Round of 32',
};

/** Combinations: n entrants in a single pool play n(n-1)/2 matches. */
const roundRobin = (n: number) => (n < 2 ? 0 : (n * (n - 1)) / 2);

/** Group sizes for a balanced draw: they differ by at most one. */
function groupSizes(entrants: number, groups: number): number[] {
  if (groups < 1 || entrants < 1) return [];
  const base = Math.floor(entrants / groups);
  const extra = entrants % groups;
  return Array.from({ length: groups }, (_, i) => base + (i < extra ? 1 : 0))
    .filter(n => n > 0)
    .sort((a, b) => a - b);
}

/**
 * Choosing between one big league and several groups.
 *
 * This was configurable in the engine but had no control anywhere in the UI,
 * so every league tournament became a single pool — which for 46 entrants is
 * 1,035 matches. The preview exists because that consequence is invisible
 * until fixtures are generated, by which point the draw is already made.
 */
export const GroupStageSettings: React.FC<GroupStageSettingsProps> = ({
  format, groupCount, qualifiersPerGroup, knockoutQualifiers, expectedEntrants,
  onGroupCountChange, onQualifiersChange, onKnockoutQualifiersChange,
  onExpectedEntrantsChange,
}) => {
  // A pure knockout has no league phase to divide.
  if (format === 'knockout') return null;

  const hasKnockout = format === 'league_knockout';
  const useGroups = groupCount > 1;

  const sizes = useGroups ? groupSizes(expectedEntrants, groupCount) : [expectedEntrants];
  const leagueMatches = sizes.reduce((sum, n) => sum + roundRobin(n), 0);

  // The bracket is a power of two, so the preview must round the same way the
  // engine does or the match count it shows is not the one that gets drawn.
  const bracketSlots = (n: number) => {
    let slots = 2;
    while (slots * 2 <= n) slots *= 2;
    return n < 2 ? 0 : slots;
  };
  const qualifiers = useGroups
    ? Math.min(groupCount * qualifiersPerGroup, expectedEntrants)
    : bracketSlots(Math.min(knockoutQualifiers, expectedEntrants));
  const knockoutMatches = hasKnockout && qualifiers >= 2 ? qualifiers - 1 : 0;
  const total = leagueMatches + knockoutMatches;

  const maxPerTeam = (sizes.length ? Math.max(...sizes) : 1) - 1;
  const heavy = leagueMatches > 250;

  return (
    <div className="p-4 bg-gray-50 rounded-xl border border-gray-200 space-y-3">
      <div>
        <h4 className="text-xs font-bold text-gray-800 flex items-center gap-1.5">
          <Layers className="w-3.5 h-3.5 text-emerald-700" />
          League Structure
        </h4>
        <p className="text-[11px] text-gray-600 mt-0.5">
          One pool where everyone plays everyone, or several smaller groups.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div>
          <label className="block text-[11px] font-bold text-gray-700 mb-1">
            Structure
          </label>
          <select
            value={groupCount}
            onChange={e => onGroupCountChange(parseInt(e.target.value) || 1)}
            className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
          >
            <option value={1}>Single league (everyone plays everyone)</option>
            {[2, 3, 4, 6, 8, 12, 16].map(n => (
              <option key={n} value={n}>{n} groups</option>
            ))}
          </select>
        </div>

        {/* ONE control for who advances, whichever shape the draw is.
            It used to be two: this dropdown, which was DISABLED for a single
            league and so did nothing whatever number you picked, and a
            separate Knockout Size panel above it. Two controls for one
            decision, one of them inert, and a summary line that ignored both
            and said "4 advance" regardless. */}
        {hasKnockout && (
          <div>
            <label className="block text-[11px] font-bold text-gray-700 mb-1">
              Who advances
            </label>
            {useGroups ? (
              <select
                value={qualifiersPerGroup}
                onChange={e => onQualifiersChange(parseInt(e.target.value) || 2)}
                className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
              >
                {[1, 2, 3, 4].map(n => (
                  <option key={n} value={n}>Top {n} per group</option>
                ))}
              </select>
            ) : (
              <select
                value={knockoutQualifiers}
                onChange={e => onKnockoutQualifiersChange(parseInt(e.target.value) || 4)}
                className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
              >
                {/* Powers of two only: any other size gives the top seeds byes
                    into round two, so the engine rounds down and the number
                    picked here would not be the number that plays. */}
                {[2, 4, 8, 16, 32].map(n => (
                  <option key={n} value={n} disabled={expectedEntrants > 0 && n > expectedEntrants}>
                    Top {n} of the league{ROUND_NAME[n] ? ` — ${ROUND_NAME[n]}` : ''}
                  </option>
                ))}
              </select>
            )}
            <p className="text-[10px] text-gray-500 mt-1">
              {useGroups
                ? `${groupCount} groups × top ${qualifiersPerGroup} = ${qualifiers} in the bracket.`
                : qualifiers >= 2
                  ? `${qualifiers} enter the bracket; ${qualifiers - 1} knockout matches.`
                  : 'Too few entrants for a knockout.'}
            </p>
          </div>
        )}

        <div>
          <label className="block text-[11px] font-bold text-gray-700 mb-1">
            Expected entrants
          </label>
          <input
            type="number"
            min={2}
            value={expectedEntrants}
            onChange={e => onExpectedEntrantsChange(Math.max(2, parseInt(e.target.value) || 2))}
            className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
          />
          <p className="text-[10px] text-gray-500 mt-1">Preview only — not saved.</p>
        </div>
      </div>

      {/* The whole point: show the size of the draw before it is made. */}
      <div className={`p-3 rounded-lg border text-[11px] ${
        heavy ? 'bg-amber-50 border-amber-300 text-amber-900'
              : 'bg-white border-gray-200 text-gray-700'
      }`}>
        <div className="flex items-start gap-2">
          {heavy ? <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                 : <Users className="w-3.5 h-3.5 mt-0.5 shrink-0 text-emerald-700" />}
          <div className="space-y-0.5">
            <div>
              <strong>{expectedEntrants} entrants</strong>
              {useGroups
                ? <> in <strong>{groupCount} groups</strong> of {sizes[0] === sizes[sizes.length - 1]
                    ? sizes[0]
                    : `${sizes[0]}–${sizes[sizes.length - 1]}`}</>
                : <> in <strong>one league</strong></>}
              {' → '}
              <strong>{leagueMatches.toLocaleString()}</strong> league match{leagueMatches === 1 ? '' : 'es'}
              {hasKnockout && knockoutMatches > 0 && <> + <strong>{knockoutMatches}</strong> knockout</>}
              {hasKnockout && knockoutMatches > 0 && <> = <strong>{total.toLocaleString()}</strong> total</>}
            </div>
            <div className="opacity-80">
              Each entrant plays {maxPerTeam} league match{maxPerTeam === 1 ? '' : 'es'}
              {hasKnockout && qualifiers >= 2 && <>, and {qualifiers} advance to the knockout</>}.
            </div>
            {heavy && (
              <div className="font-semibold pt-0.5">
                That is a lot of carrom. More groups means far fewer matches — the count grows
                with the square of the pool size.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
