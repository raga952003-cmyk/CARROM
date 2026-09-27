import React, { useEffect, useState } from 'react';
import { Registration, Tournament } from '../../types/tournament';
import { PaymentProof, paymentProofService } from '../../services/paymentProofService';

interface Props {
  tournament: Tournament;
  onChanged: () => Promise<void>;
}

/** An uploaded receipt is evidence to investigate, not confirmation of funds. */
export const PaymentProofReview: React.FC<Props> = ({ tournament, onChanged }) => {
  const [proofs, setProofs] = useState<PaymentProof[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [confirmed, setConfirmed] = useState<Record<string, boolean>>({});
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const load = async () => {
    setLoading(true);
    setError('');
    setNotice('');
    try {
      setProofs(await paymentProofService.listForTournament(tournament.id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load payment proofs.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, [tournament.id]);

  const entryName = (registrationId: string) => {
    const entry: Registration | undefined = tournament.registrations?.find(row => row.id === registrationId);
    return entry?.team?.name || entry?.player?.name || `Entry ${registrationId.slice(0, 8)}`;
  };

  const review = async (proof: PaymentProof, decision: 'approved' | 'rejected') => {
    if (busyId) return;
    const note = (notes[proof.id] || (decision === 'approved' ? 'Verified in receiving account' : '')).trim();
    if (note.length < 5) {
      setError('Enter a reason of at least 5 characters.');
      return;
    }
    if (decision === 'approved' && !confirmed[proof.id]) {
      setError('Check the actual credit in the receiving GPay or bank account before approval.');
      return;
    }
    setBusyId(proof.id);
    setError('');
    setNotice('');
    try {
      const result = await paymentProofService.review(proof.id, decision, note, decision === 'approved');
      setProofs(current => current.map(row => row.id === proof.id ? result.proof : row));
      try {
        await onChanged();
        setNotice(decision === 'approved'
          ? 'Payment verified and the entry approved.'
          : 'Payment proof rejected.');
      } catch {
        setError('The decision was saved, but the entry list could not refresh. Reload the page to see its current status.');
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not review this proof.');
    } finally {
      setBusyId(null);
    }
  };

  const pending = proofs.filter(proof => proof.status === 'pending');
  if (!tournament.gpayUpiId && proofs.length === 0 && !error) return null;

  return (
    <section className="rounded-2xl border border-blue-200 bg-blue-50 p-4 space-y-3" aria-label="GPay payment proof review">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="font-bold text-blue-950">GPay payment proofs {pending.length > 0 ? `(${pending.length} awaiting review)` : ''}</h3>
          <p className="text-xs text-blue-800">Check the transaction reference and exact amount in the receiving account; a receipt alone does not prove payment. Approving a verified payment also approves the entry.</p>
        </div>
        <button type="button" onClick={() => void load()} disabled={loading || !!busyId}
          className="rounded-lg border border-blue-300 bg-white px-3 py-1.5 text-xs font-bold text-blue-900 disabled:opacity-50">Refresh proofs</button>
      </div>
      {error && <p role="alert" className="text-xs font-semibold text-red-700">{error}</p>}
      {notice && <p role="status" className="text-xs font-semibold text-emerald-800">{notice}</p>}
      {loading ? <p className="text-xs text-blue-800">Loading proofs…</p> : pending.length === 0 ? (
        <p className="text-xs text-blue-800">No payment proofs are waiting for review.</p>
      ) : pending.map(proof => (
        <div key={proof.id} className="rounded-xl border border-blue-200 bg-white p-3 text-xs text-gray-800 space-y-2">
          <div className="flex flex-wrap justify-between gap-2">
            <strong>{entryName(proof.registrationId)}</strong>
            <span>₹{(proof.amountPaise / 100).toLocaleString('en-IN', { maximumFractionDigits: 2 })}</span>
          </div>
          <div>Transaction reference: <strong className="select-all">{proof.transactionReference}</strong></div>
          <div>Payee at submission: <strong className="select-all">{proof.payeeUpiId}</strong></div>
          <div>Submitted: {new Date(proof.submittedAt).toLocaleString()}</div>
          {proof.fileUrl ? <a href={proof.fileUrl} target="_blank" rel="noreferrer" className="font-semibold text-blue-700 underline">Open receipt</a> : (
            <p className="text-red-700">Receipt link unavailable. Refresh before reviewing.</p>
          )}
          <label className="block font-semibold">Review note
            <textarea value={notes[proof.id] || ''} onChange={event => setNotes(current => ({ ...current, [proof.id]: event.target.value }))}
              placeholder="Reason for approval or rejection" maxLength={500} rows={2}
              className="mt-1 block w-full rounded-lg border border-gray-300 p-2 font-normal" />
          </label>
          <label className="flex items-start gap-2 font-semibold text-blue-950">
            <input type="checkbox" checked={!!confirmed[proof.id]}
              onChange={event => setConfirmed(current => ({ ...current, [proof.id]: event.target.checked }))} />
            I found this exact transaction reference and amount in the receiving account.
          </label>
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={() => void review(proof, 'approved')}
              disabled={!!busyId || !proof.fileUrl || !confirmed[proof.id]}
              className="rounded-lg bg-emerald-700 px-3 py-2 font-bold text-white disabled:opacity-50">{busyId === proof.id ? 'Saving…' : 'Approve payment & entry'}</button>
            <button type="button" onClick={() => void review(proof, 'rejected')} disabled={!!busyId}
              className="rounded-lg border border-red-300 bg-red-50 px-3 py-2 font-bold text-red-800 disabled:opacity-50">Reject proof</button>
          </div>
        </div>
      ))}
    </section>
  );
};
