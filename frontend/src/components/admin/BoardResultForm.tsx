import React from 'react';
import { Flame, Crown, Circle, AlertTriangle, ShieldAlert } from 'lucide-react';
import { Match, Side, TournamentRules } from '../../types/tournament';

export interface BoardObservation {
  winner: Side;
  queenPocketedBy: Side;
  queenCoveredBy: Side;
  coinsRemainingWith: Side;
  coinsRemaining: number;
  p1Penalty: number;
  p2Penalty: number;
}

export const emptyObservation: BoardObservation = {
  winner: 'none',
  queenPocketedBy: 'none',
  queenCoveredBy: 'none',
  coinsRemainingWith: 'none',
  coinsRemaining: 0,
  p1Penalty: 0,
  p2Penalty: 0,
};

/**
 * Mirrors the backend `board_result()` so the umpire sees the score before
 * saving. The server recomputes it and stays authoritative; this is a preview.
 */
export function previewBoard(obs: BoardObservation, rules: Partial<TournamentRules>) {
  const queenPoints = rules.queenPoints ?? 3;
  const mustCover = rules.queenMustBeCovered !== false;
  const awardTo = rules.queenAwardTo ?? 'coverer';
  const warnings: string[] = [];

  let base = 0;
  if (obs.winner !== 'none' && obs.coinsRemainingWith !== 'none') {
    if (obs.coinsRemainingWith === obs.winner) {
      warnings.push('The winner is also marked as holding the coins left on the board — no base points.');
    } else {
      base = Math.max(0, obs.coinsRemaining);
    }
  }

  const covered = obs.queenCoveredBy !== 'none';
  const queenStatus: 'not_pocketed' | 'covered' | 'returned' =
    obs.queenPocketedBy === 'none' ? 'not_pocketed' : (covered || !mustCover) ? 'covered' : 'returned';

  if (covered && obs.queenPocketedBy === 'none') {
    warnings.push('The queen is marked as covered but nobody is marked as pocketing it.');
  }

  let queenSide: Side = 'none';
  let queenBonus = 0;
  if (queenStatus === 'covered' && obs.queenPocketedBy !== 'none') {
    queenSide = awardTo === 'coverer' && covered ? obs.queenCoveredBy : obs.queenPocketedBy;
    queenBonus = queenPoints;
  } else if (queenStatus === 'returned') {
    warnings.push('The queen was pocketed but not covered — it scores nothing and returns to the board.');
  }

  const pts: Record<'player1' | 'player2', number> = { player1: 0, player2: 0 };
  if (obs.winner !== 'none') pts[obs.winner] += base;
  if (queenSide !== 'none') pts[queenSide] += queenBonus;
  pts.player1 = Math.max(0, pts.player1 - Math.max(0, obs.p1Penalty));
  pts.player2 = Math.max(0, pts.player2 - Math.max(0, obs.p2Penalty));

  return { p1: pts.player1, p2: pts.player2, base, queenBonus, queenSide, queenStatus, warnings };
}

interface PickerProps {
  label: string;
  icon: React.ReactNode;
  tone: string;
  value: Side;
  onChange: (v: Side) => void;
  p1Label: string;
  p2Label: string;
  noneLabel: string;
  hint?: string;
}

const Picker: React.FC<PickerProps> = ({
  label, icon, tone, value, onChange, p1Label, p2Label, noneLabel, hint,
}) => (
  <div className={`p-3 rounded-xl border ${tone}`}>
    <label className="font-bold text-gray-800 mb-1.5 flex items-center gap-1">
      {icon}<span>{label}</span>
    </label>
    <div className="grid grid-cols-3 gap-1.5">
      {([['player1', p1Label], ['player2', p2Label], ['none', noneLabel]] as [Side, string][]).map(([v, text]) => (
        <button
          key={v}
          type="button"
          // Each picker sets only its own value. Nothing here reaches across to
          // another selection — that coupling made real boards, where the loser
          // covers the queen, impossible to record.
          onClick={() => onChange(v)}
          className={`py-1.5 px-1 rounded-lg text-center font-bold text-[11px] border transition-all truncate ${
            value === v
              ? (v === 'none' ? 'bg-gray-800 text-white border-gray-800' : 'bg-[#0B5D3B] text-white border-[#0B5D3B]')
              : 'bg-white text-gray-700 border-gray-200 hover:border-gray-300'
          }`}
        >
          {text}
        </button>
      ))}
    </div>
    {hint && <p className="text-[10px] text-gray-500 mt-1.5">{hint}</p>}
  </div>
);

interface BoardResultFormProps {
  match: Match;
  rules: Partial<TournamentRules>;
  value: BoardObservation;
  onChange: (next: BoardObservation) => void;
}

/**
 * The umpire's record of one board under remaining-coins scoring.
 *
 * Four separate questions — who won it, who sank the queen, who covered it,
 * whose coins are left — each answered on its own. The score is the
 * consequence, shown live so it can be checked before it is saved.
 */
export const BoardResultForm: React.FC<BoardResultFormProps> = ({ match, rules, value, onChange }) => {
  const p1 = match.player1Name.split(' ')[0];
  const p2 = match.player2Name.split(' ')[0];
  const coinsPerSide = rules.coinsPerSide ?? 9;
  const set = (patch: Partial<BoardObservation>) => onChange({ ...value, ...patch });
  const result = previewBoard(value, rules);

  return (
    <div className="space-y-3 text-xs">
      <Picker
        label="Finished / Won By"
        icon={<Flame className="w-3.5 h-3.5 text-emerald-700" />}
        tone="bg-emerald-50/50 border-emerald-100"
        value={value.winner}
        onChange={v => set({ winner: v })}
        p1Label={p1} p2Label={p2} noneLabel="None / Draw"
        hint="Who finished the board. This does not decide the queen."
      />

      <Picker
        label="Queen Pocketed By"
        icon={<Crown className="w-3.5 h-3.5 text-[#D4A72C]" />}
        tone="bg-amber-50/60 border-amber-200"
        value={value.queenPocketedBy}
        onChange={v => set({ queenPocketedBy: v })}
        p1Label={p1} p2Label={p2} noneLabel="Nobody"
      />

      <Picker
        label="Queen Covered By"
        icon={<Crown className="w-3.5 h-3.5 text-[#D4A72C]" />}
        tone="bg-amber-50/60 border-amber-200"
        value={value.queenCoveredBy}
        onChange={v => set({ queenCoveredBy: v })}
        p1Label={p1} p2Label={p2} noneLabel="Not Covered"
        hint="May be the opponent of whoever pocketed it. An uncovered queen scores nothing."
      />

      <div className="p-3 rounded-xl border bg-sky-50/50 border-sky-100">
        <label className="font-bold text-gray-800 mb-1.5 flex items-center gap-1">
          <Circle className="w-3.5 h-3.5 text-sky-700" /><span>Coins Remaining on Board</span>
        </label>
        <div className="grid grid-cols-3 gap-1.5">
          {([['player1', p1], ['player2', p2], ['none', 'None']] as [Side, string][]).map(([v, text]) => (
            <button
              key={v}
              type="button"
              onClick={() => set({ coinsRemainingWith: v, coinsRemaining: v === 'none' ? 0 : value.coinsRemaining })}
              className={`py-1.5 px-1 rounded-lg text-center font-bold text-[11px] border transition-all truncate ${
                value.coinsRemainingWith === v
                  ? (v === 'none' ? 'bg-gray-800 text-white border-gray-800' : 'bg-[#0B5D3B] text-white border-[#0B5D3B]')
                  : 'bg-white text-gray-700 border-gray-200 hover:border-gray-300'
              }`}
            >
              {text}
            </button>
          ))}
        </div>

        {value.coinsRemainingWith !== 'none' && (
          <div className="mt-2">
            <span className="block text-[11px] font-semibold text-gray-600 mb-1">How many coins?</span>
            <div className="flex flex-wrap gap-1">
              {Array.from({ length: coinsPerSide + 1 }, (_, n) => n).map(n => (
                <button
                  key={n}
                  type="button"
                  onClick={() => set({ coinsRemaining: n })}
                  className={`w-7 h-7 rounded-md text-[11px] font-bold border transition-all ${
                    value.coinsRemaining === n
                      ? 'bg-[#0B5D3B] text-white border-[#0B5D3B]'
                      : 'bg-white text-gray-600 border-gray-200 hover:border-gray-300'
                  }`}
                >
                  {n}
                </button>
              ))}
            </div>
          </div>
        )}

        <p className="text-[10px] text-gray-500 mt-1.5">
          The board winner scores these coins. {coinsPerSide} coins a side.
        </p>
      </div>

      <div className="p-3 rounded-xl border bg-gray-50 border-gray-200">
        <label className="font-bold text-gray-800 mb-1.5 flex items-center gap-1">
          <ShieldAlert className="w-3.5 h-3.5 text-gray-600" /><span>Penalties</span>
        </label>
        <div className="grid grid-cols-2 gap-3">
          {([['p1Penalty', match.player1Name], ['p2Penalty', match.player2Name]] as const).map(([key, name]) => (
            <div key={key}>
              <span className="block text-[11px] font-semibold text-gray-600 mb-1 truncate">{name}</span>
              <input
                type="number"
                min={0}
                value={value[key]}
                onChange={e => set({ [key]: Math.max(0, parseInt(e.target.value) || 0) } as Partial<BoardObservation>)}
                className="w-full text-sm font-bold text-center py-1.5 border border-gray-200 rounded-lg bg-white"
              />
            </div>
          ))}
        </div>
      </div>

      {/* The consequence of the answers above, before anything is saved. */}
      <div className="p-3 rounded-xl bg-[#0B5D3B] text-white">
        <div className="text-[10px] font-bold uppercase tracking-wider text-emerald-200">Board Score</div>
        <div className="mt-1 flex items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="text-[11px] text-emerald-200 truncate">{match.player1Name}</div>
            <div className="text-2xl font-black tabular-nums">{result.p1}</div>
          </div>
          <div className="text-[10px] text-emerald-200 text-center shrink-0">
            base {result.base}
            {result.queenBonus > 0 && <> · queen +{result.queenBonus}</>}
            {(value.p1Penalty > 0 || value.p2Penalty > 0) && <> · penalties applied</>}
          </div>
          <div className="min-w-0 text-right">
            <div className="text-[11px] text-emerald-200 truncate">{match.player2Name}</div>
            <div className="text-2xl font-black tabular-nums">{result.p2}</div>
          </div>
        </div>
      </div>

      {result.warnings.map((w, i) => (
        <div key={i} className="p-2.5 rounded-lg bg-amber-50 border border-amber-200 flex items-start gap-2 text-[11px] text-amber-900">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>{w}</span>
        </div>
      ))}
    </div>
  );
};
