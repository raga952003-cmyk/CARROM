import React from 'react';
import { Calculator, Info } from 'lucide-react';

export interface ScoringRules {
  scoringMode: 'classic' | 'remaining_coins';
  /** Sets per match. 1 is a flat list of boards, as before. */
  numberOfSets: number;
  /** Boards inside one set. */
  boardsPerSet: number;
  coinValue: number;
  setWinnerRule: 'total_points' | 'board_wins';
  coinsPerSide: number;
  queenPoints: number;
  queenMustBeCovered: boolean;
  queenAwardTo: 'coverer' | 'pocketer';
  tieBreak: 'additional_board' | 'sudden_death' | 'most_board_wins' | 'organizer_decision';
}

export const defaultScoringRules: ScoringRules = {
  scoringMode: 'remaining_coins',
  numberOfSets: 1,
  boardsPerSet: 8,
  coinValue: 1,
  setWinnerRule: 'total_points',
  coinsPerSide: 9,
  queenPoints: 3,
  queenMustBeCovered: true,
  queenAwardTo: 'coverer',
  tieBreak: 'additional_board',
};

interface ScoringRulesSettingsProps {
  value: ScoringRules;
  onChange: (next: ScoringRules) => void;
}

/**
 * How a board turns into points.
 *
 * Different associations score carrom differently, so the engine is driven from
 * here rather than from constants. The worked example updates as the settings
 * change, because "the winner scores the opponent's remaining coins" is easy to
 * agree with and hard to picture.
 */
export const ScoringRulesSettings: React.FC<ScoringRulesSettingsProps> = ({ value, onChange }) => {
  const set = (patch: Partial<ScoringRules>) => onChange({ ...value, ...patch });
  const remaining = value.scoringMode === 'remaining_coins';

  // A worked board: the winner pocketed everything, the loser has 4 left.
  const example = remaining
    ? { base: 4, total: 4 + value.queenPoints, loser: 0 }
    : { base: value.coinsPerSide, total: value.coinsPerSide + value.queenPoints, loser: 5 };

  return (
    <div className="p-4 bg-gray-50 rounded-xl border border-gray-200 space-y-3">
      <div>
        <h4 className="text-xs font-bold text-gray-800 flex items-center gap-1.5">
          <Calculator className="w-3.5 h-3.5 text-emerald-700" />
          Board Scoring
        </h4>
        <p className="text-[11px] text-gray-600 mt-0.5">
          How each board is turned into points.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="block text-[11px] font-bold text-gray-700 mb-1">Sets per match</label>
          <select
            value={value.numberOfSets}
            onChange={e => set({ numberOfSets: parseInt(e.target.value) || 1 })}
            className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
          >
            <option value={1}>1 set (a single run of boards)</option>
            <option value={3}>3 sets</option>
            <option value={5}>5 sets</option>
          </select>
        </div>

        <div>
          <label className="block text-[11px] font-bold text-gray-700 mb-1">Boards per set</label>
          <select
            value={value.boardsPerSet}
            onChange={e => set({ boardsPerSet: parseInt(e.target.value) || 8 })}
            className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
          >
            {[4, 6, 8, 10].map(n => <option key={n} value={n}>{n} boards</option>)}
          </select>
        </div>

        <div className="sm:col-span-2">
          <label className="block text-[11px] font-bold text-gray-700 mb-1">Scoring model</label>
          <select
            value={value.scoringMode}
            onChange={e => set({ scoringMode: e.target.value as ScoringRules['scoringMode'] })}
            className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
          >
            <option value="remaining_coins">
              Winner scores the opponent&apos;s remaining coins (tournament carrom)
            </option>
            <option value="classic">Each player scores the coins they pocketed</option>
          </select>
        </div>

        <div className="sm:col-span-2">
          <label className="block text-[11px] font-bold text-gray-700 mb-1">
            How a set is won
          </label>
          <select
            value={value.setWinnerRule}
            onChange={e => set({ setWinnerRule: e.target.value as ScoringRules['setWinnerRule'] })}
            className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
          >
            <option value="total_points">Most points across the set</option>
            <option value="board_wins">Most boards won in the set</option>
          </select>
          <p className="text-[10px] text-gray-500 mt-1">
            These can disagree — three narrow boards against one landslide.
          </p>
        </div>

        <div>
          <label className="block text-[11px] font-bold text-gray-700 mb-1">Coin value</label>
          <input
            type="number"
            min={1}
            value={value.coinValue}
            onChange={e => set({ coinValue: Math.max(1, parseInt(e.target.value) || 1) })}
            className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
          />
        </div>

        <div>
          <label className="block text-[11px] font-bold text-gray-700 mb-1">Coins per side</label>
          <input
            type="number"
            min={1}
            value={value.coinsPerSide}
            onChange={e => set({ coinsPerSide: Math.max(1, parseInt(e.target.value) || 9) })}
            className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
          />
        </div>

        <div>
          <label className="block text-[11px] font-bold text-gray-700 mb-1">Queen bonus</label>
          <input
            type="number"
            min={0}
            value={value.queenPoints}
            onChange={e => set({ queenPoints: Math.max(0, parseInt(e.target.value) || 0) })}
            className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
          />
        </div>

        <div>
          <label className="block text-[11px] font-bold text-gray-700 mb-1">Uncovered queen</label>
          <select
            value={value.queenMustBeCovered ? 'must' : 'counts'}
            onChange={e => set({ queenMustBeCovered: e.target.value === 'must' })}
            className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
          >
            <option value="must">Scores nothing, returns to the board</option>
            <option value="counts">Still scores the bonus</option>
          </select>
        </div>

        <div>
          <label className="block text-[11px] font-bold text-gray-700 mb-1">
            When the opponent covers it
          </label>
          <select
            value={value.queenAwardTo}
            onChange={e => set({ queenAwardTo: e.target.value as ScoringRules['queenAwardTo'] })}
            className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
          >
            <option value="coverer">The bonus goes to whoever covered it</option>
            <option value="pocketer">The bonus goes to whoever pocketed it</option>
          </select>
        </div>

        <div className="sm:col-span-2">
          <label className="block text-[11px] font-bold text-gray-700 mb-1">
            If the match is tied after every board
          </label>
          <select
            value={value.tieBreak}
            onChange={e => set({ tieBreak: e.target.value as ScoringRules['tieBreak'] })}
            className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
          >
            <option value="additional_board">Play an additional board</option>
            <option value="sudden_death">Sudden death</option>
            <option value="most_board_wins">Most boards won</option>
            <option value="organizer_decision">Organiser decides</option>
          </select>
        </div>
      </div>

      <div className="p-3 rounded-lg bg-white border border-gray-200 text-[11px] text-gray-700">
        <div className="flex items-start gap-2">
          <Info className="w-3.5 h-3.5 mt-0.5 shrink-0 text-emerald-700" />
          <div>
            {value.numberOfSets > 1 && (
              <div className="mb-1.5 font-semibold">
                {value.numberOfSets} sets of {value.boardsPerSet} boards is{' '}
                <strong>{value.numberOfSets * value.boardsPerSet} boards</strong>. A set is won on the
                points scored inside it and the match on sets, so a player can score fewer points
                overall and still win.
              </div>
            )}
            {remaining ? (
              <>
                A board where the winner clears their coins, the loser has <strong>4</strong> left and the
                queen is covered scores <strong>{example.total}</strong> to the winner
                ({example.base} coins + {value.queenPoints} queen) and <strong>0</strong> to the loser.
                {value.scoringMode === 'remaining_coins' && (
                  <> Every board is played out — a match is not decided early on board wins.</>
                )}
              </>
            ) : (
              <>
                Each player keeps the coins they pocketed, and the queen is added to whoever claimed it.
                A board can end <strong>{example.total}</strong>–<strong>{example.loser}</strong>.
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
