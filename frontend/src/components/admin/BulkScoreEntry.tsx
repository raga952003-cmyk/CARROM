import React, { useMemo, useState } from 'react';
import { X, Check, Loader2, AlertTriangle, Zap } from 'lucide-react';
import { Tournament, Match } from '../../types/tournament';
import { useTournament } from '../../context/TournamentContext';

interface BulkScoreEntryProps {
  tournament: Tournament;
  isOpen: boolean;
  onClose: () => void;
}

interface Row {
  match: Match;
  p1: string;
  p2: string;
}

/**
 * Enter many results in one pass.
 *
 * A league of 21 players is 210 matches; opening the live controller for each
 * one is not a realistic way to catch up after a session. This takes the
 * board-by-board scores for several matches at once and submits them through
 * the same validated endpoints, one match at a time, reporting per-row
 * outcomes rather than failing the whole batch.
 */
export const BulkScoreEntry: React.FC<BulkScoreEntryProps> = ({ tournament, isOpen, onClose }) => {
  const { submitBoardScore, confirmMatchResult, refreshData } = useTournament();

  const [category, setCategory] = useState<'all' | 'singles' | 'doubles'>('all');
  const [rows, setRows] = useState<Record<string, Row>>({});
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<{ ok: number; failed: string[] } | null>(null);

  const pending = useMemo(() => {
    return (tournament.matches || [])
      .filter(m => !m.resultConfirmed && m.player1Id && m.player2Id)
      .filter(m => category === 'all' || m.type === category)
      .sort((a, b) => a.matchNumber - b.matchNumber)
      .slice(0, 60);
  }, [tournament, category]);

  if (!isOpen) return null;

  const set = (match: Match, field: 'p1' | 'p2', value: string) => {
    setRows(prev => {
      // Spread first, then assign: a computed key widens the inferred type,
      // which loses Row and makes Object.values() unknown[].
      const existing: Row = prev[match.id] ?? { match, p1: '', p2: '' };
      const next: Row = { ...existing, match };
      next[field] = value;
      return { ...prev, [match.id]: next };
    });
  };

  // Indexed access rather than Object.values: the latter loses Row and
  // widens every element to unknown under this tsconfig.
  const filled: Row[] = Object.keys(rows)
    .map(key => rows[key])
    .filter(r => r.p1.trim() !== '' && r.p2.trim() !== '');

  const submit = async () => {
    setBusy(true);
    setResults(null);
    let ok = 0;
    const failed: string[] = [];

    for (const row of filled) {
      const p1 = Number(row.p1);
      const p2 = Number(row.p2);
      if (!Number.isFinite(p1) || !Number.isFinite(p2)) {
        failed.push(`#${row.match.matchNumber}: scores must be numbers`);
        continue;
      }
      try {
        // One board decides the match here; the engine still validates the
        // score and computes the winner server-side.
        await submitBoardScore(
          tournament.id, row.match.id, 1, p1, p2, 'none', false, 'Bulk entry'
        );
        await confirmMatchResult(tournament.id, row.match.id);
        ok += 1;
      } catch (e: any) {
        failed.push(`#${row.match.matchNumber}: ${e?.message || 'failed'}`);
      }
    }

    await refreshData();
    setResults({ ok, failed });
    setRows({});
    setBusy(false);
  };

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-black/60 backdrop-blur-xs flex items-start sm:items-center justify-center p-2 sm:p-4">
      <div className="bg-white rounded-2xl sm:rounded-3xl max-w-3xl w-full p-4 sm:p-6 shadow-2xl border border-gray-100 flex flex-col max-h-[90vh]">
        <div className="flex items-start justify-between gap-3 mb-3">
          <div>
            <span className="inline-flex items-center gap-1 text-[10px] font-bold text-[#0B5D3B] uppercase tracking-wider bg-emerald-50 px-2 py-0.5 rounded-full border border-emerald-200">
              <Zap className="w-3 h-3" /> Rapid entry
            </span>
            <h3 className="font-serif font-bold text-lg text-gray-900 mt-1">Enter several results</h3>
            <p className="text-xs text-gray-500">
              Type both scores for a match and it is submitted and confirmed. Leave a row blank to skip it.
            </p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 p-1 shrink-0">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex items-center gap-2 mb-3">
          {(['all', 'singles', 'doubles'] as const).map(c => (
            <button
              key={c}
              onClick={() => setCategory(c)}
              className={`px-3 py-1 rounded-lg text-[11px] font-bold capitalize border ${
                category === c
                  ? 'bg-[#0B5D3B] text-white border-[#0B5D3B]'
                  : 'bg-white text-gray-600 border-gray-200'
              }`}
            >
              {c}
            </button>
          ))}
          <span className="text-[11px] text-gray-500 ml-auto">
            {pending.length} awaiting a result
          </span>
        </div>

        {results && (
          <div className={`mb-3 p-3 rounded-xl border text-xs ${
            results.failed.length
              ? 'bg-amber-50 border-amber-200 text-amber-900'
              : 'bg-emerald-50 border-emerald-200 text-emerald-900'
          }`}>
            <div className="font-bold flex items-center gap-1.5">
              {results.failed.length ? <AlertTriangle className="w-4 h-4" /> : <Check className="w-4 h-4" />}
              {results.ok} result{results.ok === 1 ? '' : 's'} recorded
              {results.failed.length ? `, ${results.failed.length} could not be` : ''}
            </div>
            {results.failed.slice(0, 5).map((f, i) => (
              <div key={i} className="mt-0.5 text-[11px]">{f}</div>
            ))}
          </div>
        )}

        <div className="flex-1 overflow-y-auto -mx-1 px-1">
          {pending.length === 0 ? (
            <p className="text-xs text-gray-500 text-center py-8">
              Every match in this category already has a confirmed result.
            </p>
          ) : (
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-white">
                <tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-gray-200">
                  <th className="text-left py-2 w-10">#</th>
                  <th className="text-left py-2">Match</th>
                  <th className="text-center py-2 w-20">Score</th>
                  <th className="text-center py-2 w-20"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {pending.map(m => (
                  <tr key={m.id}>
                    <td className="py-2 font-bold text-gray-500">{m.matchNumber}</td>
                    <td className="py-2">
                      <div className="font-semibold text-gray-900 truncate max-w-[16rem]">{m.player1Name}</div>
                      <div className="font-semibold text-gray-900 truncate max-w-[16rem]">{m.player2Name}</div>
                      <div className="text-[10px] text-gray-400 uppercase tracking-wide">
                        {m.roundName} · board {m.boardNumber}
                      </div>
                    </td>
                    <td className="py-2 px-1">
                      <input
                        type="number" inputMode="numeric" min={0}
                        value={rows[m.id]?.p1 ?? ''}
                        onChange={e => set(m, 'p1', e.target.value)}
                        className="w-full p-1.5 text-center border border-gray-200 rounded-lg"
                        aria-label={`${m.player1Name} score`}
                      />
                    </td>
                    <td className="py-2 px-1">
                      <input
                        type="number" inputMode="numeric" min={0}
                        value={rows[m.id]?.p2 ?? ''}
                        onChange={e => set(m, 'p2', e.target.value)}
                        className="w-full p-1.5 text-center border border-gray-200 rounded-lg"
                        aria-label={`${m.player2Name} score`}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="pt-3 mt-3 border-t border-gray-200 flex items-center justify-between gap-3">
          <span className="text-[11px] text-gray-500">
            {filled.length} row{filled.length === 1 ? '' : 's'} ready
          </span>
          <div className="flex gap-2">
            <button onClick={onClose} className="px-4 py-2 text-xs text-gray-600 hover:bg-gray-100 rounded-lg">
              Close
            </button>
            <button
              onClick={submit}
              disabled={busy || filled.length === 0}
              className="px-4 py-2 bg-[#0B5D3B] hover:bg-[#08472d] text-white text-xs font-bold rounded-lg shadow-sm disabled:opacity-40 flex items-center gap-1.5"
            >
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
              {busy ? 'Submitting…' : `Submit ${filled.length}`}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
