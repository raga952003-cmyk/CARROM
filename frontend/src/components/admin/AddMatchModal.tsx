import React, { useMemo, useState } from 'react';
import { X, Plus, Loader2, Users, Layers, AlertTriangle } from 'lucide-react';
import { Tournament } from '../../types/tournament';
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
}

/**
 * Declared at module level on purpose. Defined inside the modal it would be a
 * fresh component type on every render, so React would tear the select down and
 * rebuild it each time state changed — and the chosen player would not stick.
 */
const EntrantPicker: React.FC<PickerProps> = ({ label, value, onChange, exclude, entrants }) => (
  <div>
    <label className="block text-[11px] font-bold text-gray-700 mb-1">{label}</label>
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
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

interface AddMatchModalProps {
  tournament: Tournament;
  onClose: () => void;
  onAdded: () => void;
}

/** Rounds an organiser actually names, plus a free-text escape. */
const ROUNDS: { stage: 'league' | 'knockout'; label: string }[] = [
  { stage: 'league', label: 'League' },
  { stage: 'knockout', label: 'Pre-Quarter Final' },
  { stage: 'knockout', label: 'Quarter Final' },
  { stage: 'knockout', label: 'Semi Final' },
  { stage: 'knockout', label: 'Final' },
  { stage: 'knockout', label: 'Third Place Play-off' },
];

/**
 * Add one fixture to a draw that already exists.
 *
 * Regenerating fixtures rebuilds every match and throws away the boards already
 * scored, so once play has started it is not an option. A player who entered
 * late, a rematch the referee ordered, or a play-off the format does not
 * produce all need exactly one match added beside the rest.
 */
export const AddMatchModal: React.FC<AddMatchModalProps> = ({ tournament, onClose, onAdded }) => {
  const { addManualMatch } = useTournament();

  const entrants: Entrant[] = useMemo(() => {
    const approved = (tournament.registrations || []).filter(r => r.status === 'approved');
    return approved
      .map(r => {
        // A doubles entry is a team; a singles entry is the player themselves.
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

  const [roundIndex, setRoundIndex] = useState(0);
  const [customRound, setCustomRound] = useState('');
  const [p1, setP1] = useState('');
  const [p2, setP2] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const round = ROUNDS[roundIndex];
  const one = entrants.find(e => e.id === p1);
  const two = entrants.find(e => e.id === p2);

  // The server refuses these too; catching them here saves a round trip and
  // explains the problem next to the control that caused it.
  const problem =
    !p1 || !p2 ? 'Choose both players.'
    : p1 === p2 ? 'A player cannot be fixtured against themselves.'
    : one && two && one.type !== two.type
      ? 'A singles player cannot be fixtured against a doubles team.'
      : '';

  const save = async () => {
    if (problem) { setError(problem); return; }
    setBusy(true);
    setError('');
    try {
      await addManualMatch(tournament.id, {
        stage: round.stage,
        roundName: customRound.trim() || round.label,
        player1Id: p1,
        player2Id: p2,
      });
      onAdded();
      onClose();
    } catch (e: any) {
      setError(e?.message || 'Could not add the match.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-start sm:items-center justify-center p-3 overflow-y-auto">
      <div className="bg-white rounded-2xl w-full max-w-lg shadow-2xl my-6">
        <div className="px-5 py-4 bg-[#0B5D3B] text-white rounded-t-2xl flex items-center gap-3">
          <Plus className="w-5 h-5 text-[#D4A72C]" />
          <div className="flex-1 min-w-0">
            <h3 className="font-serif font-bold">Add a Match</h3>
            <p className="text-[11px] text-emerald-200">
              One fixture, added to the draw already in play.
            </p>
          </div>
          <button onClick={onClose} aria-label="Close" className="p-1 rounded-lg hover:bg-white/10">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          {entrants.length < 2 ? (
            <div className="p-3 rounded-xl bg-amber-50 border border-amber-200 text-[11px] text-amber-900 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
              <span>
                This tournament has fewer than two approved entrants. Approve the
                registrations first, then add the match.
              </span>
            </div>
          ) : (
            <>
              <div>
                <label className="block text-[11px] font-bold text-gray-700 mb-1 flex items-center gap-1">
                  <Layers className="w-3.5 h-3.5 text-emerald-700" /> Stage
                </label>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5">
                  {ROUNDS.map((r, i) => (
                    <button
                      key={r.label}
                      type="button"
                      onClick={() => { setRoundIndex(i); setCustomRound(''); }}
                      className={`py-2 px-1 rounded-lg text-[11px] font-bold border transition-all truncate ${
                        roundIndex === i && !customRound
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
                  value={customRound}
                  onChange={e => setCustomRound(e.target.value)}
                  placeholder="or type your own round name"
                  className="mt-2 w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-[1fr_auto_1fr] gap-2 items-end">
                <EntrantPicker label="Player 1" value={p1} onChange={setP1} exclude={p2} entrants={entrants} />
                <span className="hidden sm:block text-[11px] font-bold text-gray-400 pb-2 text-center">vs</span>
                <EntrantPicker label="Player 2" value={p2} onChange={setP2} exclude={p1} entrants={entrants} />
              </div>

              {/* The fixture as it will read on the schedule. */}
              <div className="p-3 rounded-xl bg-gray-50 border border-gray-200 text-center">
                <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">
                  {customRound.trim() || round.label}
                </div>
                <div className="mt-1 text-sm font-bold text-gray-900 flex items-center justify-center gap-2">
                  <Users className="w-3.5 h-3.5 text-gray-400" />
                  <span className="truncate max-w-[38%]">{one?.name || 'Player 1'}</span>
                  <span className="text-gray-400">vs</span>
                  <span className="truncate max-w-[38%]">{two?.name || 'Player 2'}</span>
                </div>
              </div>

              {(error || problem) && (
                <div className={`p-3 rounded-xl border text-xs ${
                  error ? 'bg-red-50 border-red-200 text-red-800'
                        : 'bg-gray-50 border-gray-200 text-gray-600'
                }`}>
                  {error || problem}
                </div>
              )}
            </>
          )}
        </div>

        <div className="px-5 py-3 bg-[#F8F6F0] rounded-b-2xl flex items-center justify-end gap-3">
          <button onClick={onClose} className="px-4 py-2 text-xs font-bold text-gray-700 hover:bg-gray-200 rounded-xl">
            Cancel
          </button>
          <button
            onClick={save}
            disabled={busy || !!problem || entrants.length < 2}
            className="px-5 py-2 text-xs font-bold bg-[#0B5D3B] hover:bg-[#08472d] text-white rounded-xl shadow-md flex items-center gap-1.5 disabled:opacity-40"
          >
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            <span>{busy ? 'Adding…' : 'Add Match'}</span>
          </button>
        </div>
      </div>
    </div>
  );
};
