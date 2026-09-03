import React, { useEffect, useMemo, useState } from 'react';
import { compareMatches } from '../../utils/matchOrder';
import { ArrowLeft, Minus, Plus, Play, Check, Crown, Loader2 } from 'lucide-react';
import { Tournament, Match } from '../../types/tournament';
import { useTournament } from '../../context/TournamentContext';
import { apiClient } from '../../utils/apiClient';
import { messageOf } from '../../context/NotificationContext';
import { ReasonModal } from '../common/ReasonModal';
import { exitToApp } from '../../utils/useHashRoute';
import { MatchTimer } from '../admin/MatchTimer';
import { BoardResultForm, BoardObservation, emptyObservation, previewBoard } from '../admin/BoardResultForm';

interface BoardModeProps {
  boardNumber: number;
  tournamentId?: string;
}

/**
 * One score, with a big target either side of it for a thumb.
 *
 * Declared here rather than inside BoardMode. A component defined during a
 * render is a NEW component type on every render, so React unmounted and
 * rebuilt this whole subtree each time -- which meant the number input was
 * destroyed and recreated on every keystroke, taking the focus and the caret
 * with it. Entering a two-digit score meant tapping the field again between
 * the digits. It happened on every realtime refresh too, mid-entry.
 *
 * The setter is passed, not a plain callback: each tap must derive from the
 * latest value. With `onChange(value + 1)` every tap in a quick burst read
 * the same rendered `value`, so rapid tapping on a phone silently dropped
 * increments.
 */
const Stepper: React.FC<{
  label: string;
  value: number;
  onChange: React.Dispatch<React.SetStateAction<number>>;
  highlight: boolean;
}> = ({ label, value, onChange, highlight }) => (
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
    confirmMatchResult, addBoardToMatch,
  } = useTournament();

  const tournament: Tournament | undefined =
    (tournamentId && tournaments.find(t => t.id === tournamentId)) || currentTournament;

  const [p1, setP1] = useState(0);
  const [p2, setP2] = useState(0);
  const [queen, setQueen] = useState<'player1' | 'player2' | 'none'>('none');
  // Defaults to covered, which is the ordinary case: a queen that was
  // pocketed and not covered has to be returned to the board.
  const [queenCovered, setQueenCovered] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  // Declared here, with the rest, because two early returns sit between this
  // point and where it used to live. Hooks must run in the same order on every
  // render: the first render has no tournament loaded and took the `!tournament`
  // branch after nine hooks, then the render after the data arrived reached the
  // tenth -- and React tears the screen down with "Rendered more hooks than
  // during the previous render". That is the umpire's phone, mid-match.
  const [obs, setObs] = useState<BoardObservation>(emptyObservation);
  // The side a level match is about to be awarded to; the ReasonModal is open
  // while this is set. Up here with the others for the same reason as `obs`.
  const [tieBreakTarget, setTieBreakTarget] = useState<{ id: string; name: string } | null>(null);

  // Matches on this board that still need playing, in running order.
  const queue = useMemo(() => {
    if (!tournament) return [];
    return (tournament.matches || [])
      .filter(m => (m.boardNumber || 1) === boardNumber && !m.resultConfirmed)
      .filter(m => m.player1Id && m.player2Id)
      .sort((a, b) =>
        compareMatches(a, b));
  }, [tournament, boardNumber]);

  // Prefer whatever is already live on this board, else the next one up.
  const match: Match | undefined =
    queue.find(m => m.status === 'live' || m.status === 'paused') || queue[0];

  // The board waiting to be scored, taken in set order.
  //
  // A board is identified by its set AND its number -- numbering restarts at 1
  // in every set -- and this used to pick by number alone and submit without a
  // set. The server defaults a missing set to 1, so the moment a multi-set
  // match reached set 2, scoring its board 1 rewrote board 1 of SET 1: a
  // played result silently replaced, and the set the umpire was actually on
  // never filled.
  const activeBoard = useMemo(() => {
    if (!match) return null;
    const boards = [...(match.boards || [])].sort((a, b) =>
      ((a.setNumber || 1) - (b.setNumber || 1)) || (a.boardNumber - b.boardNumber));
    return boards.find(b => b.status === 'in_progress')
      || boards.find(b => b.status === 'pending')
      || null;
  }, [match]);

  // A new board, or a new match, starts blank. `obs` was left out of this:
  // under remaining-coins scoring the classic fields reset and the observation
  // -- the winner, the queen, the coins left on the board -- did not, so it
  // carried into the next fixture on this board and the umpire had to notice
  // and clear it themselves.
  useEffect(() => {
    setP1(0); setP2(0); setQueen('none'); setError('');
    setObs(emptyObservation);
  }, [match?.id, activeBoard?.boardNumber]);

  const target = match?.targetPoints || tournament?.rules?.targetScore || 29;

  // Says whether the write went through, so a modal can stay open on a refusal
  // and close only on success.
  const run = async (fn: () => Promise<unknown>, success?: string): Promise<boolean> => {
    if (busy) return false;
    setBusy(true); setError(''); setNote('');
    try {
      await fn();
      if (success) setNote(success);
      return true;
    } catch (e) {
      setError(messageOf(e, 'That did not work.'));
      return false;
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

  const rules: any = tournament.rules || {};
  const usesRemainingCoins = rules.scoringMode === 'remaining_coins';

  const decided = match.status === 'completed' || !!match.winnerId;

  const awardTieBreak = async (reason: string) => {
    if (!tieBreakTarget) return;
    const { id: winnerId, name } = tieBreakTarget;
    const ok = await run(
      () => apiClient.post(`/matches/${match.id}/tie-break`, { winnerId, reason }),
      `Awarded to ${name}.`
    );
    if (ok) setTieBreakTarget(null);
  };

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
            {(match.isTimerRunning || match.timerElapsedSeconds > 0) && (
              <span className="px-2 py-0.5 rounded-full bg-emerald-900/70 tabular-nums">
                <MatchTimer match={match} />
              </span>
            )}
          </div>
          {match.tossWinnerName && (
            <div className="mt-1.5 text-[11px] text-emerald-200">
              Toss: <strong>{match.tossWinnerName}</strong> chose {match.tossChoice || 'strike'}
            </div>
          )}
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

        {/* All boards played and the scores level. `decided` is false and
            `activeBoard` is undefined, so this screen used to render nothing
            below the header -- the umpire is left holding a phone with no
            controls and no explanation, at the exact moment they need telling
            what to do. */}
        {!decided && !activeBoard && match.tieBreakRequired && (
          <div className="p-4 rounded-2xl bg-amber-50 border-2 border-amber-300 space-y-3">
            <div>
              <div className="text-sm font-black text-amber-900">This match finished level</div>
              <div className="text-xs text-amber-800 mt-0.5">
                Both players scored {match.player1TotalPoints} across all{' '}
                {(match.boards || []).length} boards.
                {match.tieBreakRule === 'additional_board'
                  ? ' Add a deciding board and play it.'
                  : ' Award it to one of them.'}
              </div>
            </div>
            {match.tieBreakRule === 'additional_board' && (
              <button
                onClick={() => run(() => addBoardToMatch(tournament.id, match.id), 'Deciding board added.')}
                disabled={busy}
                className="w-full py-3.5 rounded-2xl bg-[#0B5D3B] text-white font-black text-sm disabled:opacity-50"
              >
                Add a deciding board
              </button>
            )}
            <div className="grid grid-cols-2 gap-2">
              {[
                { id: match.player1Id, name: match.player1Name },
                { id: match.player2Id, name: match.player2Name },
              ].filter(p => p.id).map(p => (
                <button
                  key={p.id}
                  onClick={() => { setError(''); setTieBreakTarget({ id: p.id!, name: p.name }); }}
                  disabled={busy}
                  className="py-3 rounded-2xl bg-white border-2 border-amber-300 text-gray-800 font-bold text-xs disabled:opacity-50"
                >
                  Award to {p.name}
                </button>
              ))}
            </div>
          </div>
        )}

        {!decided && activeBoard && (
          <>
            <div>
              <div className="text-[11px] font-bold uppercase tracking-wider text-gray-500">
                Board {activeBoard.boardNumber} of {match.maxBoards} · first to {target}
              </div>
              <div className="text-[11px] text-gray-500 mt-0.5">
                {usesRemainingCoins
                  ? 'Record what happened. The winner scores the coins left on the board.'
                  : 'Enter coins only — the queen is added automatically.'}
              </div>
            </div>
            {usesRemainingCoins ? (
              <BoardResultForm match={match} rules={rules} value={obs} onChange={setObs} />
            ) : (
              <>
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
              {/* Without this the queen was always submitted as uncovered, and
                  apply_queen_points scores an uncovered queen as zero -- so a
                  queen taken on the phone was silently worth nothing, while the
                  label above promised it counted when covered. */}
              {queen !== 'none' && (
                <div className="mt-2">
                  <div className="text-xs font-bold text-gray-600 mb-1.5">
                    Was it covered?
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    {([[true, 'Covered'], [false, 'Not covered']] as const).map(([value, label]) => (
                      <button
                        key={String(value)}
                        type="button"
                        onClick={() => setQueenCovered(value)}
                        className={`py-2 px-1 rounded-xl border text-xs font-bold ${
                          queenCovered === value
                            ? 'bg-[#0B5D3B] border-[#0B5D3B] text-white'
                            : 'bg-white border-gray-200 text-gray-600'
                        }`}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
              </>
            )}

            <button
              disabled={busy || (usesRemainingCoins
                ? obs.winner === 'none' && obs.queenPocketedBy === 'none'
                : p1 === 0 && p2 === 0)}
              onClick={() => run(async () => {
                // Which set the board belongs to travels with it; without it
                // the server writes to set 1 whatever the umpire is scoring.
                const setNumber = activeBoard.setNumber;
                if (usesRemainingCoins) {
                  const preview = previewBoard(obs, rules);
                  await submitBoardScore(tournament.id, match.id, activeBoard.boardNumber, {
                    ...(setNumber ? { setNumber } : {}),
                    p1Score: preview.p1,
                    p2Score: preview.p2,
                    boardWinner: obs.winner,
                    coinsRemainingWith: obs.coinsRemainingWith,
                    coinsRemaining: obs.coinsRemaining,
                    queenPocketedBy: obs.queenPocketedBy,
                    queenCoveredBy: obs.queenCoveredBy,
                    p1Penalty: obs.p1Penalty,
                    p2Penalty: obs.p2Penalty,
                    auditReason: 'Board mode',
                  });
                  setObs(emptyObservation);
                } else {
                  await submitBoardScore(tournament.id, match.id, activeBoard.boardNumber, {
                    ...(setNumber ? { setNumber } : {}),
                    p1Score: p1, p2Score: p2,
                    queenClaimedBy: queen, queenCovered,
                    auditReason: 'Board mode',
                  });
                  setP1(0); setP2(0); setQueen('none');
                }
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

        {/* The page-level error box sits behind this overlay while it is open,
            so the same message is passed in to be read here. */}
        <ReasonModal
          isOpen={!!tieBreakTarget}
          onClose={() => setTieBreakTarget(null)}
          onConfirm={awardTieBreak}
          title={`Award this match to ${tieBreakTarget?.name || ''}?`}
          description="The scores are level, so this is the umpire's ruling. Say why — it is recorded with the result."
          placeholder="e.g. Umpire ruling — opponent conceded the deciding board"
          confirmLabel={`Award to ${tieBreakTarget?.name || ''}`}
          busy={busy}
          error={tieBreakTarget ? error : ''}
          variant="warning"
        />
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
