import React, { useMemo, useState } from 'react';
import { X, Pencil, Loader2, Users, Layers, AlertTriangle, CalendarClock } from 'lucide-react';
import { Tournament, Match } from '../../types/tournament';
import { useTournament } from '../../context/TournamentContext';

interface Entrant {
  id: string;
  name: string;
  type: 'singles' | 'doubles';
}

interface PickerProps {
  label: string;
  value: string;
  onChange: (v: string) => void;
  exclude: string;
  entrants: Entrant[];
  disabled?: boolean;
}

/**
 * Module level, like the one in AddMatchModal and for the same reason: defined
 * inside the modal it would be a new component type on every render, so React
 * would rebuild the select each keystroke and the chosen player would not stick.
 */
const EntrantPicker: React.FC<PickerProps> = ({
  label, value, onChange, exclude, entrants, disabled,
}) => (
  <div>
    <label className="block text-[11px] font-bold text-gray-700 mb-1">{label}</label>
    <select
      value={value}
      disabled={disabled}
      onChange={e => onChange(e.target.value)}
      className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white disabled:bg-gray-100 disabled:text-gray-500"
    >
      <option value="">Select…</option>
      {entrants.filter(e => e.id !== exclude).map(e => (
        <option key={e.id} value={e.id}>
          {e.name}{e.type === 'doubles' ? ' (doubles)' : ''}
        </option>
      ))}
    </select>
  </div>
);

interface EditMatchModalProps {
  tournament: Tournament;
  match: Match;
  onClose: () => void;
  onSaved: () => void;
}

/** The same rounds Add Match offers, so an edited fixture can be named alike. */
const ROUNDS: { stage: 'league' | 'knockout'; label: string }[] = [
  { stage: 'league', label: 'League' },
  { stage: 'knockout', label: 'Pre-Quarter Final' },
  { stage: 'knockout', label: 'Quarter Final' },
  { stage: 'knockout', label: 'Semi Final' },
  { stage: 'knockout', label: 'Final' },
  { stage: 'knockout', label: 'Third Place Play-off' },
];

/** Whether this fixture has anything on it that a re-pairing would inherit. */
const hasPlay = (m: Match) =>
  !!m.resultConfirmed
  || m.status === 'live' || m.status === 'paused' || m.status === 'completed'
  || (m.boards || []).some(b =>
    b.status === 'completed' || !!b.player1Score || !!b.player2Score);

/**
 * Edit one fixture: who plays it, what it is called, and when and where.
 *
 * The draw could be added to and removed from but never corrected, so a
 * mis-entered pairing had to be deleted and made again — losing its match
 * number and anything already scored on it.
 *
 * Deliberately not the result. Scores and the winner belong to the scoring
 * screen; this changes the fixture around them.
 */
export const EditMatchModal: React.FC<EditMatchModalProps> = ({
  tournament, match, onClose, onSaved,
}) => {
  const { updateMatchFixture } = useTournament();

  const entrants: Entrant[] = useMemo(() => {
    const approved = (tournament.registrations || []).filter(r => r.status === 'approved');
    return approved
      .map(r => {
        if (r.type === 'doubles' && r.team) {
          return { id: r.team.id, name: r.team.name || 'Team', type: 'doubles' as const };
        }
        if (r.player) {
          return { id: r.player.id, name: r.player.name || 'Player', type: 'singles' as const };
        }
        return null;
      })
      .filter(Boolean) as Entrant[];
  }, [tournament.registrations]);

  const played = hasPlay(match);

  const [p1, setP1] = useState(match.player1Id || '');
  const [p2, setP2] = useState(match.player2Id || '');
  const [roundName, setRoundName] = useState(match.roundName || '');
  const [stage, setStage] = useState<'league' | 'knockout'>(
    match.stage === 'knockout' ? 'knockout' : 'league');
  const [boardNumber, setBoardNumber] = useState(String(match.boardNumber ?? 1));
  const [scheduledDate, setScheduledDate] = useState(match.scheduledDate || '');
  const [scheduledTime, setScheduledTime] = useState(match.scheduledTime || '');
  const [reason, setReason] = useState('');
  // Re-pairing a played fixture is the one edit that needs saying out loud.
  const [acceptRepair, setAcceptRepair] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [warnings, setWarnings] = useState<string[]>([]);

  const one = entrants.find(e => e.id === p1);
  const two = entrants.find(e => e.id === p2);

  const repairing = p1 !== (match.player1Id || '') || p2 !== (match.player2Id || '');
  const movingStage = stage !== (match.stage === 'knockout' ? 'knockout' : 'league');
  const needsForce = played && (repairing || movingStage);

  const board = parseInt(boardNumber, 10);

  const problem =
    p1 && p2 && p1 === p2 ? 'A player cannot be fixtured against themselves.'
    : one && two && one.type !== two.type
      ? 'A singles player cannot be fixtured against a doubles team.'
    : boardNumber !== '' && (!Number.isFinite(board) || board < 1)
      ? 'Board number starts at 1.'
      : '';

  // Only what actually differs is sent, so the server writes nothing it need not.
  const changes = useMemo(() => {
    const out: Record<string, any> = {};
    if (p1 && p1 !== (match.player1Id || '')) out.player1Id = p1;
    if (p2 && p2 !== (match.player2Id || '')) out.player2Id = p2;
    if (roundName.trim() && roundName.trim() !== match.roundName) out.roundName = roundName.trim();
    if (movingStage) out.stage = stage;
    if (Number.isFinite(board) && board !== match.boardNumber) out.boardNumber = board;
    if (scheduledDate !== (match.scheduledDate || '')) out.scheduledDate = scheduledDate;
    if (scheduledTime !== (match.scheduledTime || '')) out.scheduledTime = scheduledTime;
    return out;
  }, [p1, p2, roundName, stage, board, scheduledDate, scheduledTime, match, movingStage]);

  const nothingToDo = Object.keys(changes).length === 0;

  const save = async () => {
    if (problem) { setError(problem); return; }
    if (nothingToDo) { setError('Nothing has changed.'); return; }
    setBusy(true);
    setError('');
    setWarnings([]);
    try {
      const result = await updateMatchFixture(
        tournament.id, match.id,
        reason.trim() ? { ...changes, reason: reason.trim() } : changes,
        needsForce);
      const raised = result?.warnings || [];
      if (raised.length) {
        // Saved, but the organiser has just double-booked something. Show it
        // here rather than closing over the top of it.
        setWarnings(raised);
        onSaved();
        return;
      }
      onSaved();
      onClose();
    } catch (e: any) {
      setError(e?.message || 'Could not update the fixture.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-start sm:items-center justify-center p-3 overflow-y-auto">
      <div className="bg-white rounded-2xl w-full max-w-lg shadow-2xl my-6">
        <div className="px-5 py-4 bg-[#0B5D3B] text-white rounded-t-2xl flex items-center gap-3">
          <Pencil className="w-5 h-5 text-[#D4A72C]" />
          <div className="flex-1 min-w-0">
            <h3 className="font-serif font-bold">Edit Match #{match.matchNumber}</h3>
            <p className="text-[11px] text-emerald-200">
              The fixture and its schedule. Scores are changed on the scoring screen.
            </p>
          </div>
          <button onClick={onClose} aria-label="Close" className="p-1 rounded-lg hover:bg-white/10">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          {played && (
            <div className="p-3 rounded-xl bg-amber-50 border border-amber-200 text-[11px] text-amber-900 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
              <span>
                This fixture has play recorded on it. Moving its board or time is
                safe; changing who plays it keeps the existing board scores under
                the new pairing and rewrites the points table.
              </span>
            </div>
          )}

          {/* Who plays */}
          <div className="grid grid-cols-1 sm:grid-cols-[1fr_auto_1fr] gap-2 items-end">
            <EntrantPicker
              label="Player 1" value={p1} onChange={setP1} exclude={p2}
              entrants={entrants} disabled={entrants.length < 2} />
            <span className="hidden sm:block text-[11px] font-bold text-gray-400 pb-2 text-center">vs</span>
            <EntrantPicker
              label="Player 2" value={p2} onChange={setP2} exclude={p1}
              entrants={entrants} disabled={entrants.length < 2} />
          </div>

          {/* What it is called */}
          <div>
            <label className="block text-[11px] font-bold text-gray-700 mb-1 flex items-center gap-1">
              <Layers className="w-3.5 h-3.5 text-emerald-700" /> Round
            </label>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5">
              {ROUNDS.map(r => (
                <button
                  key={r.label}
                  type="button"
                  onClick={() => { setRoundName(r.label); setStage(r.stage); }}
                  className={`py-2 px-1 rounded-lg text-[11px] font-bold border transition-all truncate ${
                    roundName === r.label
                      ? 'bg-[#0B5D3B] text-white border-[#0B5D3B]'
                      : 'bg-white text-gray-700 border-gray-200 hover:border-gray-300'
                  }`}
                >
                  {r.label}
                </button>
              ))}
            </div>
            <input
              type="text"
              value={roundName}
              onChange={e => setRoundName(e.target.value)}
              placeholder="Round name"
              className="mt-2 w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
            />
            <div className="mt-2 flex items-center gap-2 text-[11px]">
              <span className="font-bold text-gray-700">Stage:</span>
              {(['league', 'knockout'] as const).map(s => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setStage(s)}
                  className={`px-2.5 py-1 rounded-lg font-bold capitalize border transition-colors ${
                    stage === s
                      ? 'bg-emerald-100 text-emerald-900 border-emerald-300'
                      : 'bg-white text-gray-600 border-gray-200'
                  }`}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>

          {/* When and where */}
          <div>
            <label className="block text-[11px] font-bold text-gray-700 mb-1 flex items-center gap-1">
              <CalendarClock className="w-3.5 h-3.5 text-emerald-700" /> Schedule
            </label>
            <div className="grid grid-cols-3 gap-2">
              <input
                type="number" min={1} value={boardNumber}
                onChange={e => setBoardNumber(e.target.value)}
                placeholder="Board"
                aria-label="Board number"
                className="text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
              />
              <input
                type="date" value={scheduledDate}
                onChange={e => setScheduledDate(e.target.value)}
                aria-label="Scheduled date"
                className="text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
              />
              <input
                type="text" value={scheduledTime}
                onChange={e => setScheduledTime(e.target.value)}
                placeholder="9:00 AM"
                aria-label="Scheduled time"
                className="text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
              />
            </div>
          </div>

          {/* The fixture as it will read on the schedule. */}
          <div className="p-3 rounded-xl bg-gray-50 border border-gray-200 text-center">
            <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">
              {roundName || 'Round'} · Board {boardNumber || '—'}
              {scheduledTime ? ` · ${scheduledTime}` : ''}
            </div>
            <div className="mt-1 text-sm font-bold text-gray-900 flex items-center justify-center gap-2">
              <Users className="w-3.5 h-3.5 text-gray-400" />
              <span className="truncate max-w-[38%]">{one?.name || match.player1Name}</span>
              <span className="text-gray-400">vs</span>
              <span className="truncate max-w-[38%]">{two?.name || match.player2Name}</span>
            </div>
          </div>

          {needsForce && (
            <label className="flex items-start gap-2 p-3 rounded-xl bg-red-50 border border-red-200 text-[11px] text-red-900 cursor-pointer">
              <input
                type="checkbox"
                checked={acceptRepair}
                onChange={e => setAcceptRepair(e.target.checked)}
                className="mt-0.5"
              />
              <span>
                I understand this fixture has been played, and the boards already
                scored will stay on it under the new pairing.
              </span>
            </label>
          )}

          {needsForce && (
            <input
              type="text"
              value={reason}
              onChange={e => setReason(e.target.value)}
              placeholder="Why (recorded in the audit log)"
              className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
            />
          )}

          {warnings.length > 0 && (
            <div className="p-3 rounded-xl bg-amber-50 border border-amber-200 text-xs text-amber-900">
              <div className="font-bold mb-1">Saved, but check the schedule:</div>
              <ul className="list-disc list-inside space-y-0.5">
                {warnings.map((w, i) => <li key={i}>{w}</li>)}
              </ul>
            </div>
          )}

          {(error || problem) && (
            <div className={`p-3 rounded-xl border text-xs ${
              error ? 'bg-red-50 border-red-200 text-red-800'
                    : 'bg-gray-50 border-gray-200 text-gray-600'
            }`}>
              {error || problem}
            </div>
          )}
        </div>

        <div className="px-5 py-3 bg-[#F8F6F0] rounded-b-2xl flex items-center justify-end gap-3">
          <button onClick={onClose} className="px-4 py-2 text-xs font-bold text-gray-700 hover:bg-gray-200 rounded-xl">
            {warnings.length ? 'Close' : 'Cancel'}
          </button>
          <button
            onClick={save}
            disabled={busy || !!problem || nothingToDo || (needsForce && !acceptRepair)}
            className="px-5 py-2 text-xs font-bold bg-[#0B5D3B] hover:bg-[#08472d] text-white rounded-xl shadow-md flex items-center gap-1.5 disabled:opacity-40"
          >
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Pencil className="w-4 h-4" />}
            <span>{busy ? 'Saving…' : 'Save Changes'}</span>
          </button>
        </div>
      </div>
    </div>
  );
};
