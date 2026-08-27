import React from 'react';
import { Layers, Users, Info } from 'lucide-react';

interface GroupStageSettingsProps {
  format: string;
  groupCount: number;
  qualifiersPerGroup: number;
  expectedEntrants: number;
  onGroupCountChange: (n: number) => void;
  onQualifiersChange: (n: number) => void;
  onExpectedEntrantsChange: (n: number) => void;
}

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
  format, groupCount, qualifiersPerGroup, expectedEntrants,
  onGroupCountChange, onQualifiersChange, onExpectedEntrantsChange,
}) => {
  // A pure knockout has no league phase to divide.
  if (format === 'knockout') return null;

  const hasKnockout = format === 'league_knockout';
  const useGroups = groupCount > 1;

  const sizes = useGroups ? groupSizes(expectedEntrants, groupCount) : [expectedEntrants];
  const leagueMatches = sizes.reduce((sum, n) => sum + roundRobin(n), 0);

  const qualifiers = useGroups
    ? Math.min(groupCount * qualifiersPerGroup, expectedEntrants)
    : Math.min(4, expectedEntrants);
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

        {hasKnockout && (
          <div>
            <label className="block text-[11px] font-bold text-gray-700 mb-1">
              Who advances
            </label>
            <select
              value={qualifiersPerGroup}
              onChange={e => onQualifiersChange(parseInt(e.target.value) || 2)}
              disabled={!useGroups}
              className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white disabled:opacity-50"
            >
              {[1, 2, 3, 4].map(n => (
                <option key={n} value={n}>Top {n} per group</option>
              ))}
            </select>
            {!useGroups && (
              <p className="text-[10px] text-gray-500 mt-1">Top 4 of the league advance.</p>
            )}
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
