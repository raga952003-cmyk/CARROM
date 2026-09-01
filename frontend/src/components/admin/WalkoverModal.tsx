import React, { useState } from 'react';
import { X, UserX, Loader2, AlertTriangle } from 'lucide-react';
import { Match } from '../../types/tournament';
import { apiClient } from '../../utils/apiClient';
import { useNotify } from '../../context/NotificationContext';

/**
 * Award a match nobody played.
 *
 * A player fails to arrive, retires injured, or concedes. Until this existed
 * the only way to finish a match was to score boards, so an organiser facing an
 * absent player had to invent board scores — which then sat in the points table
 * looking exactly like a played result.
 *
 * The reason is required rather than optional. A walkover is the one result
 * that cannot be reconstructed from the boards later, so if the record does not
 * say why, nobody will ever know.
 */

interface Props {
  match: Match;
  onClose: () => void;
  onDone: () => void;
}

const PRESETS = [
  'Opponent did not arrive',
  'Retired injured',
  'Conceded',
  'Withdrew from the tournament',
];

export const WalkoverModal: React.FC<Props> = ({ match, onClose, onDone }) => {
  const notify = useNotify();
  const [winnerId, setWinnerId] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);

  const sides = [
    { id: match.player1Id, name: match.player1Name },
    { id: match.player2Id, name: match.player2Name },
  ].filter(s => s.id);

  const loser = sides.find(s => s.id !== winnerId);
  const problem = !winnerId ? 'Choose who takes the match.'
    : !reason.trim() ? 'Say why — a walkover cannot be reconstructed later.'
    : '';

  const save = async () => {
    if (problem || busy) return;
    setBusy(true);
    try {
      const res: any = await apiClient.post(`/matches/${match.id}/walkover`, {
        winnerId, reason: reason.trim(),
      });
      // The API degrades rather than blocking when migration 010 is missing,
      // and says so; passing that straight through beats a silent half-success.
      if (res?.warning) notify.error(res.warning);
      else notify.success('Walkover recorded.');
      onDone();
      onClose();
    } catch (e) {
      notify.report(e, 'Could not record the walkover.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-start sm:items-center justify-center p-3 overflow-y-auto">
      <div className="bg-white rounded-2xl w-full max-w-md shadow-2xl my-6">
        <div className="px-5 py-4 bg-[#0B5D3B] text-white rounded-t-2xl flex items-center gap-3">
          <UserX className="w-5 h-5 text-[#D4A72C]" />
          <div className="flex-1 min-w-0">
            <h3 className="font-serif font-bold">Record a Walkover</h3>
            <p className="text-xs text-emerald-200">
              Match #{match.matchNumber} — a result decided off the board
            </p>
          </div>
          <button onClick={onClose} aria-label="Close" className="p-1 rounded-lg hover:bg-white/10">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          <div>
            <label className="block text-xs font-bold text-gray-700 mb-1.5">
              Who takes the match?
            </label>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {sides.map(s => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => setWinnerId(s.id!)}
                  className={`py-3 px-3 rounded-xl text-sm font-bold border-2 transition-all ${
                    winnerId === s.id
                      ? 'bg-[#0B5D3B] text-white border-[#0B5D3B]'
                      : 'bg-white text-gray-700 border-gray-200 hover:border-gray-400'
                  }`}
                >
                  {s.name}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="block text-xs font-bold text-gray-700 mb-1.5">
              Why? (recorded on the result)
            </label>
            <div className="flex flex-wrap gap-1.5 mb-2">
              {PRESETS.map(p => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setReason(p)}
                  className={`px-2.5 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                    reason === p
                      ? 'bg-emerald-50 border-emerald-400 text-emerald-900'
                      : 'bg-white border-gray-200 text-gray-600 hover:border-gray-400'
                  }`}
                >
                  {p}
                </button>
              ))}
            </div>
            <input
              type="text"
              value={reason}
              onChange={e => setReason(e.target.value)}
              placeholder="or type your own"
              className="w-full text-sm px-3 py-2.5 border border-gray-200 rounded-lg bg-white"
            />
          </div>

          {winnerId && (
            <div className="p-3 rounded-xl bg-gray-50 border border-gray-200 text-xs text-gray-700">
              <span className="font-bold">{sides.find(s => s.id === winnerId)?.name}</span> takes
              the match; <span className="font-bold">{loser?.name}</span> is recorded as not
              having played it. No coins are scored, so the result cannot distort the
              points-difference tie-break.
            </div>
          )}

          {problem && (
            <div className="flex items-start gap-2 p-3 rounded-xl bg-amber-50 border border-amber-200 text-xs text-amber-900">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
              <span>{problem}</span>
            </div>
          )}
        </div>

        <div className="px-5 py-3 bg-[#F8F6F0] rounded-b-2xl flex items-center justify-end gap-3">
          <button onClick={onClose} className="px-4 py-2 text-xs font-bold text-gray-700 hover:bg-gray-200 rounded-xl">
            Cancel
          </button>
          <button
            onClick={save}
            disabled={!!problem || busy}
            className="px-5 py-2 text-xs font-bold bg-[#0B5D3B] hover:bg-[#08472d] text-white rounded-xl shadow-md flex items-center gap-1.5 disabled:opacity-40"
          >
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <UserX className="w-4 h-4" />}
            <span>{busy ? 'Recording…' : 'Record Walkover'}</span>
          </button>
        </div>
      </div>
    </div>
  );
};
