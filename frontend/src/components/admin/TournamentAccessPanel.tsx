import React, { useCallback, useEffect, useState } from 'react';
import { Lock, ShieldCheck, Clock, UserPlus, Check, X, Loader2, Ban } from 'lucide-react';
import {
  accessService, AccessRequest, AccessRole, GrantableAdmin, TournamentAccess,
} from '../../services/accessService';
import { useNotify } from '../../context/NotificationContext';

/**
 * Who may run this tournament.
 *
 * Whoever creates a tournament owns it. Another admin can see it but cannot
 * score or change anything until the owner lets them in. There are two ways in
 * and this panel is both of them: the visitor asks and the owner approves, or
 * the owner adds them without being asked — which is the usual case on the day,
 * when the organiser already knows who is scoring on table three.
 *
 * The panel shows each person only what applies to them, because an admin who
 * is waiting on a decision and an owner deciding are different jobs.
 */

interface Props {
  tournamentId: string;
  tournamentName: string;
  access: TournamentAccess;
  /** Called after anything changes, so the parent can re-read its own access. */
  onChanged: () => void;
}

const ROLE_LABEL: Record<AccessRole, string> = {
  manager: 'Manage (full control)',
  scorer: 'Score only',
};

export const TournamentAccessPanel: React.FC<Props> = ({
  tournamentId, tournamentName, access, onChanged,
}) => {
  const notify = useNotify();

  const [requests, setRequests] = useState<AccessRequest[]>([]);
  const [admins, setAdmins] = useState<GrantableAdmin[]>([]);
  const [mine, setMine] = useState<AccessRequest | null>(null);
  const [busyId, setBusyId] = useState<string>('');
  const [role, setRole] = useState<AccessRole>('scorer');
  const [message, setMessage] = useState('');
  const [grantTo, setGrantTo] = useState('');
  const [grantRole, setGrantRole] = useState<AccessRole>('scorer');

  const isOwner = access.isOwner;

  const reload = useCallback(async () => {
    if (isOwner) {
      const [reqs, adms] = await Promise.all([
        accessService.listRequests(tournamentId).catch(() => [] as AccessRequest[]),
        accessService.grantableAdmins().catch(() => [] as GrantableAdmin[]),
      ]);
      setRequests(reqs || []);
      setAdmins(adms || []);
    } else {
      const res = await accessService.listMine().catch(() => null);
      setMine((res?.requests || []).find(r => r.tournamentId === tournamentId) || null);
    }
  }, [isOwner, tournamentId]);

  useEffect(() => { reload(); }, [reload]);

  // Ownership not being enforced makes this whole panel meaningless: every
  // admin already has everything it would grant.
  if (!access.enforced && !isOwner) return null;

  const run = async (id: string, fn: () => Promise<unknown>, done: string) => {
    setBusyId(id);
    try {
      await fn();
      notify.success(done);
      await reload();
      onChanged();
    } catch (e) {
      notify.report(e);
    } finally {
      setBusyId('');
    }
  };

  const pending = requests.filter(r => r.status === 'pending');
  const granted = requests.filter(r => r.status === 'approved');

  return (
    <div className="bg-white rounded-2xl border border-gray-200 shadow-xs overflow-hidden">
      <div className="px-4 py-3 bg-[#F8F6F0] border-b border-gray-200 flex items-center gap-2">
        <Lock className="w-4 h-4 text-[#0B5D3B]" />
        <h3 className="text-xs font-bold text-gray-800">Who can run this tournament</h3>
      </div>

      <div className="p-4 space-y-4">
        {isOwner ? (
          <>
            <p className="text-[11px] text-gray-600 leading-relaxed">
              You created <span className="font-bold">{tournamentName}</span>, so you control it.
              Anyone else needs your approval before they can score or change anything.
            </p>

            {/* Waiting on a decision. */}
            <div>
              <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500 mb-1.5">
                Awaiting your decision ({pending.length})
              </div>
              {pending.length === 0 ? (
                <p className="text-[11px] text-gray-400 italic">Nobody is waiting.</p>
              ) : (
                <div className="space-y-1.5">
                  {pending.map(r => (
                    <div key={r.id} className="p-2.5 rounded-xl bg-amber-50 border border-amber-200">
                      <div className="flex items-start gap-2">
                        <Clock className="w-3.5 h-3.5 text-amber-600 mt-0.5 shrink-0" />
                        <div className="flex-1 min-w-0">
                          <div className="text-xs font-bold text-gray-900 truncate">
                            {r.userName || r.userEmail || 'An admin'}
                          </div>
                          <div className="text-[10px] text-amber-800">
                            asked for {ROLE_LABEL[r.accessRole] || r.accessRole}
                          </div>
                          {r.message && (
                            <div className="text-[10px] text-gray-600 italic mt-0.5">"{r.message}"</div>
                          )}
                        </div>
                      </div>
                      <div className="flex items-center gap-1.5 mt-2">
                        <button
                          onClick={() => run(r.id, () => accessService.approve(r.id, r.accessRole), 'Access granted.')}
                          disabled={!!busyId}
                          className="px-3 py-1.5 text-[11px] font-bold bg-[#0B5D3B] hover:bg-[#08472d] text-white rounded-lg flex items-center gap-1 disabled:opacity-40"
                        >
                          {busyId === r.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                          Approve
                        </button>
                        <button
                          onClick={() => run(r.id, () => accessService.reject(r.id), 'Request declined.')}
                          disabled={!!busyId}
                          className="px-3 py-1.5 text-[11px] font-bold text-gray-700 hover:bg-gray-100 rounded-lg border border-gray-200 flex items-center gap-1 disabled:opacity-40"
                        >
                          <X className="w-3 h-3" /> Decline
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Already in. */}
            {granted.length > 0 && (
              <div>
                <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500 mb-1.5">
                  Helping you run it ({granted.length})
                </div>
                <div className="space-y-1.5">
                  {granted.map(r => (
                    <div key={r.id} className="flex items-center gap-2 p-2.5 rounded-xl bg-emerald-50 border border-emerald-200">
                      <ShieldCheck className="w-3.5 h-3.5 text-emerald-600 shrink-0" />
                      <div className="flex-1 min-w-0">
                        <div className="text-xs font-bold text-gray-900 truncate">
                          {r.userName || r.userEmail}
                        </div>
                        <div className="text-[10px] text-emerald-800">
                          {ROLE_LABEL[r.accessRole] || r.accessRole}
                        </div>
                      </div>
                      <button
                        onClick={() => run(r.id, () => accessService.revoke(r.id), 'Access revoked.')}
                        disabled={!!busyId}
                        className="px-2.5 py-1 text-[10px] font-bold text-red-700 hover:bg-red-100 rounded-lg border border-red-200 flex items-center gap-1 disabled:opacity-40"
                      >
                        <Ban className="w-3 h-3" /> Revoke
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Straight to it, without waiting to be asked. */}
            <div className="pt-3 border-t border-gray-200">
              <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500 mb-1.5">
                Add someone directly
              </div>
              <div className="flex flex-col sm:flex-row gap-1.5">
                <select
                  value={grantTo}
                  onChange={e => setGrantTo(e.target.value)}
                  className="flex-1 text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
                >
                  <option value="">Choose an admin…</option>
                  {admins.map(a => (
                    <option key={a.id} value={a.id}>{a.name} ({a.email})</option>
                  ))}
                </select>
                <select
                  value={grantRole}
                  onChange={e => setGrantRole(e.target.value as AccessRole)}
                  className="text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
                >
                  <option value="scorer">Score only</option>
                  <option value="manager">Manage</option>
                </select>
                <button
                  onClick={() => run('grant',
                    () => accessService.grant(tournamentId, { userId: grantTo }, grantRole),
                    'Access granted.')}
                  disabled={!grantTo || !!busyId}
                  className="px-4 py-2 text-xs font-bold bg-[#0B5D3B] hover:bg-[#08472d] text-white rounded-lg shadow-xs flex items-center justify-center gap-1.5 disabled:opacity-40"
                >
                  {busyId === 'grant' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <UserPlus className="w-3.5 h-3.5" />}
                  Add
                </button>
              </div>
              {admins.length === 0 && (
                <p className="text-[10px] text-gray-400 italic mt-1.5">
                  No other admin accounts exist yet. They need to register first.
                </p>
              )}
            </div>
          </>
        ) : (
          /* Not the owner: ask, or wait, or know you already have it. */
          <>
            {mine?.status === 'pending' ? (
              <div className="flex items-start gap-2 p-3 rounded-xl bg-amber-50 border border-amber-200">
                <Clock className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
                <div className="text-[11px] text-amber-900">
                  <span className="font-bold block">Waiting on the owner</span>
                  You asked for {ROLE_LABEL[mine.accessRole] || mine.accessRole}. Until they
                  approve it you can watch this tournament but not change it.
                </div>
              </div>
            ) : access.canScore ? (
              <div className="flex items-start gap-2 p-3 rounded-xl bg-emerald-50 border border-emerald-200">
                <ShieldCheck className="w-4 h-4 text-emerald-600 mt-0.5 shrink-0" />
                <div className="text-[11px] text-emerald-900">
                  <span className="font-bold block">
                    You have {access.canManage ? 'full' : 'scoring'} access
                  </span>
                  Granted by the tournament owner.
                </div>
              </div>
            ) : (
              <>
                <p className="text-[11px] text-gray-600 leading-relaxed">
                  {mine?.status === 'rejected'
                    ? 'The owner declined your last request. You can ask again.'
                    : mine?.status === 'revoked'
                      ? 'Your access to this tournament was withdrawn. You can ask again.'
                      : `Someone else created ${tournamentName}. Ask them for access to help run it.`}
                  {mine?.decisionNote && (
                    <span className="block mt-1 italic text-gray-500">"{mine.decisionNote}"</span>
                  )}
                </p>
                <div className="flex flex-col sm:flex-row gap-1.5">
                  <select
                    value={role}
                    onChange={e => setRole(e.target.value as AccessRole)}
                    className="text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
                  >
                    <option value="scorer">Score only</option>
                    <option value="manager">Manage</option>
                  </select>
                  <input
                    type="text"
                    value={message}
                    onChange={e => setMessage(e.target.value)}
                    placeholder="Why you need it (optional)"
                    className="flex-1 text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
                  />
                  <button
                    onClick={() => run('request',
                      () => accessService.requestAccess(tournamentId, role, message.trim() || undefined),
                      'Request sent to the owner.')}
                    disabled={!!busyId}
                    className="px-4 py-2 text-xs font-bold bg-[#0B5D3B] hover:bg-[#08472d] text-white rounded-lg shadow-xs flex items-center justify-center gap-1.5 disabled:opacity-40"
                  >
                    {busyId === 'request' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <UserPlus className="w-3.5 h-3.5" />}
                    Request access
                  </button>
                </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
};
