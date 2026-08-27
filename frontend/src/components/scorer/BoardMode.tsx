import React, { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, Minus, Plus, Play, Check, Crown, Loader2 } from 'lucide-react';
import { Tournament, Match } from '../../types/tournament';
import { useTournament } from '../../context/TournamentContext';
import { exitToApp } from '../../utils/useHashRoute';

interface BoardModeProps {
  boardNumber: number;
  tournamentId?: string;
}

/**
 * Scoring screen for one board (spec 73).
 *
 * Sized for a phone held at the board: the current match, two large score
 * steppers and one submit. Scoring previously lived inside the admin
 * dashboard, which assumes a desk and a wide screen.
 */
export const BoardMode: React.FC<BoardModeProps> = ({ boardNumber, tournamentId }) => {
  const {
    tournaments, currentTournament, startMatch, submitBoardScore,
    confirmMatchResult, refreshData,
  } = useTournament();

  const tournament: Tournament | undefined =
    (tournamentId && tournaments.find(t => t.id === tournamentId)) || currentTournament;

  const [p1, setP1] = useState(0);
  const [p2, setP2] = useState(0);
  const [queen, setQueen] = useState<'player1' | 'player2' | 'none'>('none');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [note, setNote] = useState('');

  // Matches on this board that still need playing, in running order.
  const queue = useMemo(() => {
    if (!tournament) return [];
    return (tournament.matches || [])
      .filter(m => (m.boardNumber || 1) === boardNumber && !m.resultConfirmed)
      .filter(m => m.player1Id && m.player2Id)
      .sort((a, b) =>
        (a.scheduledDate || '').localeCompare(b.scheduledDate || '') ||
        (a.scheduledTime || '').localeCompare(b.scheduledTime || '') ||
        a.matchNumber - b.matchNumber);
  }, [tournament, boardNumber]);

  // Prefer whatever is already live on this board, else the next one up.
  const match: Match | undefined =
    queue.find(m => m.status === 'live' || m.status === 'paused') || queue[0];

  const activeBoard = useMemo(() => {
    if (!match) return null;
    const boards = match.boards || [];
    return boards.find(b => b.status === 'in_progress')
      || boards.find(b => b.status === 'pending')
      || null;
  }, [match]);

  useEffect(() => {
    setP1(0); setP2(0); setQueen('none'); setError('');
  }, [match?.id, activeBoard?.boardNumber]);

  const target = match?.targetPoints || tournament?.rules?.targetScore || 29;

  const run = async (fn: () => Promise<void>, success?: string) => {
    setBusy(true); setError(''); setNote('');
    try {
      await fn();
      if (success) setNote(success);
    } catch (e: any) {
      setError(e?.message || 'That did not work.');
    } finally {
      setBusy(false);
    }
  };

  if (!tournament) {
    return (
      <Shell boardNumber={boardNumber}>
        <p className="text-sm text-gray-600">No tournament selected. Open one from the dashboard first.</p>
      </Shell>
    );
  }

  if (!match) {
    return (
      <Shell boardNumber={boardNumber} tournamentName={tournament.name}>
        <div className="text-center py-10">
          <Check className="w-10 h-10 mx-auto text-emerald-500 mb-2" />
          <p className="text-sm font-bold text-gray-800">Nothing left on board {boardNumber}</p>
          <p className="text-xs text-gray-500 mt-1">Every match assigned here is finished.</p>
        </div>
      </Shell>
    );
  }

  const decided = match.status === 'completed' || !!match.winnerId;
  // The setter is passed, not a plain callback: each tap must derive from the
  // latest value. With `onChange(value + 1)` every tap in a quick burst read
  // the same rendered `value`, so rapid tapping on a phone silently dropped
  // increments.
  const Stepper = ({
    label, value, onChange, highlight,
  }: {
    label: string;
    value: number;
    onChange: React.Dispatch<React.SetStateAction<number>>;
    highlight: boolean;
  }) => (
    <div className={`rounded-2xl border-2 p-3 ${highlight ? 'border-[#0B5D3B] bg-emerald-50/60' : 'border-gray-200'}`}>
      <div className="text-[11px] font-bold text-gray-700 truncate mb-2">{label}</div>
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          aria-label={`Decrease ${label}`}
          onClick={() => onChange(v => Math.max(0, v - 1))}
          className="w-12 h-12 rounded-xl bg-gray-100 active:bg-gray-200 flex items-center justify-center shrink-0"
        >
          <Minus className="w-5 h-5" />
        </button>
        <input
          type="number"
          inputMode="numeric"
          value={value}
          onChange={e => onChange(Math.max(0, Number(e.target.value) || 0))}
          className="w-full text-center text-3xl font-black text-gray-900 bg-transparent outline-hidden"
        />
        <button
          type="button"
          aria-label={`Increase ${label}`}
          onClick={() => onChange(v => v + 1)}
          className="w-12 h-12 rounded-xl bg-[#0B5D3B] text-white active:bg-[#08472d] flex items-center justify-center shrink-0"
        >
          <Plus className="w-5 h-5" />
        </button>
      </div>
    </div>
  );

  return (
    <Shell boardNumber={boardNumber} tournamentName={tournament.name}>
      <div className="space-y-4">
        <div className="rounded-2xl bg-[#0B5D3B] text-white p-4">
          <div className="flex items-center justify-between text-[10px] uppercase tracking-wider text-emerald-200">
            <span>Match #{match.matchNumber} · {match.roundName}</span>
            <span>{match.scheduledTime || 'unscheduled'}</span>
          </div>
          <div className="mt-2 text-lg font-bold leading-tight">{match.player1Name}</div>
          <div className="text-[11px] text-emerald-300 my-0.5">versus</div>
          <div className="text-lg font-bold leading-tight">{match.player2Name}</div>
          <div className="mt-2 flex items-center gap-2 text-[11px]">
            <span className="px-2 py-0.5 rounded-full bg-emerald-900/70">{match.type}</span>
            <span className="px-2 py-0.5 rounded-full bg-emerald-900/70">
              boards {match.player1BoardWins}–{match.player2BoardWins}
            </span>
            <span className="px-2 py-0.5 rounded-full bg-emerald-900/70 capitalize">{match.status}</span>
          </div>
        </div>

        {error && (
          <div className="p-3 rounded-xl bg-red-50 border border-red-200 text-xs text-red-800">{error}</div>
        )}
        {note && (
          <div className="p-3 rounded-xl bg-emerald-50 border border-emerald-200 text-xs text-emerald-900">{note}</div>
        )}

        {match.status === 'scheduled' && (
          <button
            disabled={busy}
            onClick={() => run(() => startMatch(tournament.id, match.id), 'Match started.')}
            className="w-full py-4 rounded-2xl bg-[#D4A72C] text-[#202522] font-black text-sm flex items-center justify-center gap-2 disabled:opacity-50"
          >
            {busy ? <Loader2 className="w-5 h-5 animate-spin" /> : <Play className="w-5 h-5" />}
            Start match
          </button>
        )}

        {!decided && activeBoard && (
          <>
            <div>
              <div className="text-[11px] font-bold uppercase tracking-wider text-gray-500">
                Board {activeBoard.boardNumber} of {match.maxBoards} · first to {target}
              </div>
              <div className="text-[11px] text-gray-500 mt-0.5">
                Enter coins only — the queen is added automatically.
              </div>
            </div>
            <Stepper label={match.player1Name} value={p1} onChange={setP1} highlight={p1 > p2} />
            <Stepper label={match.player2Name} value={p2} onChange={setP2} highlight={p2 > p1} />

            <div>
              <div className="text-[11px] font-bold text-gray-600 mb-1.5">
                Queen taken by <span className="font-normal text-gray-400">(only scores if covered)</span>
              </div>
              <div className="grid grid-cols-3 gap-2">
                {([
                  ['none', 'Nobody'],
                  ['player1', match.player1Name],
                  ['player2', match.player2Name],
                ] as const).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setQueen(value)}
                    className={`py-2 px-1 rounded-xl border text-[11px] font-bold truncate ${
                      queen === value
                        ? 'bg-[#D4A72C] border-[#D4A72C] text-[#202522]'
                        : 'bg-white border-gray-200 text-gray-600'
                    }`}
                  >
                    {value !== 'none' && <Crown className="w-3 h-3 inline mr-1" />}
                    {label}
                  </button>
                ))}
              </div>
            </div>

            <button
              disabled={busy || (p1 === 0 && p2 === 0)}
              onClick={() => run(async () => {
                await submitBoardScore(
                  tournament.id, match.id, activeBoard.boardNumber,
                  p1, p2, queen, false, 'Board mode'
                );
                setP1(0); setP2(0); setQueen('none');
              }, `Board ${activeBoard.boardNumber} recorded.`)}
              className="w-full py-4 rounded-2xl bg-[#0B5D3B] text-white font-black text-sm flex items-center justify-center gap-2 disabled:opacity-40"
            >
              {busy ? <Loader2 className="w-5 h-5 animate-spin" /> : <Check className="w-5 h-5" />}
              Submit board {activeBoard.boardNumber}
            </button>
          </>
        )}

        {decided && !match.resultConfirmed && (
          <div className="space-y-3">
            <div className="p-4 rounded-2xl bg-amber-50 border border-amber-200 text-center">
              <Crown className="w-6 h-6 mx-auto text-[#D4A72C]" />
              <div className="text-sm font-bold text-gray-900 mt-1">
                {match.winnerName || 'Drawn'}
              </div>
              <div className="text-[11px] text-gray-600">
                {match.player1BoardWins}–{match.player2BoardWins} on boards
              </div>
            </div>
            <button
              disabled={busy}
              onClick={() => run(async () => {
                await confirmMatchResult(tournament.id, match.id);
                await refreshData();
              }, 'Result confirmed. Next match loaded.')}
              className="w-full py-4 rounded-2xl bg-[#0B5D3B] text-white font-black text-sm disabled:opacity-50"
            >
              {busy ? 'Confirming…' : 'Confirm result'}
            </button>
          </div>
        )}

        <div className="pt-2 text-[11px] text-gray-500 text-center">
          {queue.length - 1} more match{queue.length - 1 === 1 ? '' : 'es'} queued on this board
        </div>
      </div>
    </Shell>
  );
};

const Shell: React.FC<{
  boardNumber: number;
  tournamentName?: string;
  children: React.ReactNode;
}> = ({ boardNumber, tournamentName, children }) => (
  <div className="min-h-screen bg-[#F8F6F0]">
    <header className="sticky top-0 z-10 bg-[#0B5D3B] text-white px-4 py-3 flex items-center gap-3">
      <button onClick={exitToApp} aria-label="Back" className="p-1.5 -ml-1.5 rounded-lg hover:bg-emerald-900/60">
        <ArrowLeft className="w-5 h-5" />
      </button>
      <div className="min-w-0">
        <div className="font-black text-base leading-none">Board {boardNumber}</div>
        {tournamentName && (
          <div className="text-[11px] text-emerald-200 truncate mt-0.5">{tournamentName}</div>
        )}
      </div>
    </header>
    <main className="max-w-md mx-auto px-4 py-4">{children}</main>
  </div>
);
