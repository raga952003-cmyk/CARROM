import React from 'react';
import { Check, Circle, Trophy } from 'lucide-react';
import { Match, BoardScore, MatchSet } from '../../types/tournament';

/**
 * Per-set totals, derived from the boards the client already holds.
 *
 * Mirrors `summarise_sets()` on the server so the umpire sees the set standing
 * without a round trip after every board. The server recomputes it and stays
 * authoritative — this is a view, never the stored value.
 */
export function summariseSets(match: Match, boardsPerSet: number): MatchSet[] {
  const totalSets = Math.max(1, match.numberOfSets || 1);
  const grouped = new Map<number, BoardScore[]>();
  for (const b of match.boards || []) {
    const n = b.setNumber || 1;
    if (!grouped.has(n)) grouped.set(n, []);
    grouped.get(n)!.push(b);
  }

  const out: MatchSet[] = [];
  for (let setNumber = 1; setNumber <= totalSets; setNumber++) {
    const members = grouped.get(setNumber) || [];
    let p1 = 0, p2 = 0, done = 0;
    for (const b of members) {
      if (b.status !== 'completed') continue;
      done += 1;
      p1 += b.player1Score || 0;
      p2 += b.player2Score || 0;
    }
    const expected = members.length || boardsPerSet;
    const complete = done > 0 && done >= expected;
    out.push({
      setNumber,
      status: complete ? 'completed' : done ? 'in_progress' : 'pending',
      boardsCompleted: done,
      boardsExpected: expected,
      player1Points: p1,
      player2Points: p2,
      winnerId: complete ? (p1 > p2 ? match.player1Id : p2 > p1 ? match.player2Id : null) : null,
      winnerName: complete ? (p1 > p2 ? match.player1Name : p2 > p1 ? match.player2Name : null) : null,
    });
  }
  return out;
}

interface SetScoreboardProps {
  match: Match;
  boardsPerSet: number;
  activeSet: number;
  onSelectSet: (n: number) => void;
}

/**
 * The set standing of a match: which set is being played, what each one
 * finished at, and how many each side has won.
 *
 * A match played in sets is won on sets, not on total points, so the running
 * point total on its own can say the opposite of who is actually ahead.
 */
export const SetScoreboard: React.FC<SetScoreboardProps> = ({
  match, boardsPerSet, activeSet, onSelectSet,
}) => {
  const sets = summariseSets(match, boardsPerSet);
  if (sets.length <= 1) return null;

  const p1Sets = sets.filter(s => s.winnerId && s.winnerId === match.player1Id).length;
  const p2Sets = sets.filter(s => s.winnerId && s.winnerId === match.player2Id).length;

  return (
    <div className="bg-white rounded-2xl border border-gray-200/80 shadow-xs overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between gap-3">
        <h4 className="font-serif font-bold text-gray-900 text-sm">Sets</h4>
        <div className="flex items-center gap-2 text-xs">
          <span className="truncate max-w-[110px] text-gray-600">{match.player1Name}</span>
          <span className="px-2 py-0.5 rounded-lg bg-[#0B5D3B] text-white font-black tabular-nums">
            {p1Sets}–{p2Sets}
          </span>
          <span className="truncate max-w-[110px] text-gray-600">{match.player2Name}</span>
        </div>
      </div>

      {/* One tab per set. The umpire scores into whichever is selected. */}
      <div className="flex gap-1.5 p-3 overflow-x-auto">
        {sets.map(s => {
          const selected = s.setNumber === activeSet;
          const wonBy1 = s.winnerId && s.winnerId === match.player1Id;
          const wonBy2 = s.winnerId && s.winnerId === match.player2Id;
          return (
            <button
              key={s.setNumber}
              type="button"
              onClick={() => onSelectSet(s.setNumber)}
              className={`shrink-0 px-3 py-2 rounded-xl border text-left transition-all ${
                selected ? 'border-[#0B5D3B] bg-emerald-50/70' : 'border-gray-200 hover:border-gray-300'
              }`}
            >
              <div className="flex items-center gap-1.5">
                {s.status === 'completed'
                  ? <Check className="w-3.5 h-3.5 text-emerald-700" />
                  : <Circle className={`w-3.5 h-3.5 ${s.status === 'in_progress' ? 'text-[#D4A72C]' : 'text-gray-300'}`} />}
                <span className="text-[11px] font-bold text-gray-900">Set {s.setNumber}</span>
              </div>
              <div className="mt-0.5 text-sm font-black tabular-nums text-gray-900">
                {s.player1Points}–{s.player2Points}
              </div>
              <div className="text-[10px] text-gray-500">
                {s.status === 'completed'
                  ? (s.winnerName ? `${s.winnerName.split(' ')[0]} won` : 'drawn')
                  : `${s.boardsCompleted}/${s.boardsExpected} boards`}
              </div>
              {(wonBy1 || wonBy2) && (
                <Trophy className="w-3 h-3 text-[#D4A72C] mt-0.5" />
              )}
            </button>
          );
        })}
      </div>

      {/* The full grid, board by board, once more than one set has started. */}
      {sets.some(s => s.status !== 'pending') && (
        <div className="px-3 pb-3 overflow-x-auto">
          <table className="w-full text-[11px] border-collapse">
            <thead>
              <tr className="text-gray-500">
                <th className="text-left font-semibold py-1 pr-2">Set</th>
                <th className="text-right font-semibold py-1 px-2 truncate">{match.player1Name}</th>
                <th className="text-right font-semibold py-1 px-2 truncate">{match.player2Name}</th>
                <th className="text-left font-semibold py-1 pl-2">Winner</th>
              </tr>
            </thead>
            <tbody>
              {sets.map(s => (
                <tr key={s.setNumber} className="border-t border-gray-100">
                  <td className="py-1 pr-2 font-semibold text-gray-700">Set {s.setNumber}</td>
                  <td className={`py-1 px-2 text-right tabular-nums font-bold ${
                    s.winnerId === match.player1Id ? 'text-[#0B5D3B]' : 'text-gray-700'
                  }`}>{s.player1Points}</td>
                  <td className={`py-1 px-2 text-right tabular-nums font-bold ${
                    s.winnerId === match.player2Id ? 'text-[#0B5D3B]' : 'text-gray-700'
                  }`}>{s.player2Points}</td>
                  <td className="py-1 pl-2 text-gray-600 truncate">
                    {s.status === 'completed' ? (s.winnerName || 'Drawn') : '—'}
                  </td>
                </tr>
              ))}
              <tr className="border-t-2 border-gray-200">
                <td className="py-1 pr-2 font-black text-gray-900">Sets won</td>
                <td className="py-1 px-2 text-right tabular-nums font-black text-gray-900">{p1Sets}</td>
                <td className="py-1 px-2 text-right tabular-nums font-black text-gray-900">{p2Sets}</td>
                <td className="py-1 pl-2 font-bold text-gray-900 truncate">
                  {p1Sets === p2Sets ? '—'
                    : (p1Sets > p2Sets ? match.player1Name : match.player2Name)}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
