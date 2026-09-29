import React, { useEffect, useState } from 'react';
import { Registration, Tournament } from '../../types/tournament';
import { PaymentProof, paymentProofService } from '../../services/paymentProofService';

interface GPayPaymentProofProps {
  registration: Registration;
  tournament: Tournament;
  onPendingChange?: (pending: boolean | null) => void;
}

/** A receipt starts a review; it never marks the registration as paid. */
export const GPayPaymentProof: React.FC<GPayPaymentProofProps> = ({ registration, tournament, onPendingChange }) => {
  const [proofs, setProofs] = useState<PaymentProof[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [reference, setReference] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [recipientConfirmed, setRecipientConfirmed] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    onPendingChange?.(null);
    paymentProofService.listForRegistration(registration.id)
      .then(rows => {
        if (active) {
          setProofs(rows);
          onPendingChange?.(rows.some(proof => proof.status === 'pending'));
        }
      })
      .catch(err => {
        if (active) {
          setError(err instanceof Error ? err.message : 'Could not load payment proofs.');
          onPendingChange?.(null);
        }
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [registration.id]);

  const refreshStatus = async () => {
    setLoading(true);
    setError('');
    try {
      const rows = await paymentProofService.listForRegistration(registration.id);
      setProofs(rows);
      onPendingChange?.(rows.some(proof => proof.status === 'pending'));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load payment proofs.');
      onPendingChange?.(null);
    } finally {
      setLoading(false);
    }
  };

  const pending = proofs.some(proof => proof.status === 'pending');
  const latest = proofs[0];
  const amount = (registration.feePaise ?? Math.round(Number(tournament.entryFee || 0) * 100)) / 100;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (submitting || pending || !file) return;
    if (!recipientConfirmed) {
      setError('Confirm that you paid the exact tournament UPI recipient shown here.');
      return;
    }
    const normalizedReference = reference.replace(/[^A-Za-z0-9]/g, '').toUpperCase();
    if (normalizedReference.length < 6 || normalizedReference.length > 80) {
      setError('Enter a UPI transaction reference of 6 to 80 letters or digits.');
      return;
    }
    if (file.size === 0 || file.size > 5 * 1024 * 1024 ||
        !['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
      setError('Choose a JPEG, PNG, or WebP receipt image up to 5 MB.');
      return;
    }
    setSubmitting(true);
    setError('');
    setMessage('');
    try {
      const result = await paymentProofService.submit(registration.id, normalizedReference, file);
      setProofs(current => [result.proof, ...current]);
      onPendingChange?.(true);
      setMessage(result.message);
      setFile(null);
      setReference('');
      setRecipientConfirmed(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not upload the receipt.');
    } finally {
      setSubmitting(false);
    }
  };

  if (!tournament.gpayUpiId || registration.paymentStatus !== 'pending' || registration.status === 'rejected' ||
      tournament.status === 'completed' || tournament.status === 'cancelled') return null;

  return (
    <div className="rounded-xl border border-blue-200 bg-blue-50 p-4 text-xs text-blue-950 space-y-3">
      <div>
        <h4 className="font-bold text-sm">Direct UPI payment · ₹{amount.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</h4>
        <p className="mt-1">Send payment to this tournament's UPI ID: <strong className="select-all break-all">{tournament.gpayUpiId}</strong></p>
        <p className="mt-1 font-semibold text-red-800">Check the recipient before paying. Sending the same amount to any other UPI ID does not pay this tournament.</p>
        <p className="mt-1 text-blue-800">Use this method only if you are not paying through Razorpay. Upload the receipt and transaction reference after paying. An identical receipt or reused transaction ID is rejected; similar screenshots are flagged for review. Your entry stays pending until an organiser checks the actual credit in the receiving account.</p>
        <button type="button" onClick={() => void refreshStatus()} disabled={loading || submitting}
          className="mt-2 rounded-lg border border-blue-300 bg-white px-3 py-1.5 font-semibold text-blue-900 disabled:opacity-50">
          Refresh proof status
        </button>
      </div>

      {loading ? <p>Loading payment proof status…</p> : latest && (
        <div className="rounded-lg bg-white border border-blue-100 p-2">
          <strong>Latest proof: {latest.status === 'pending' ? 'awaiting review' : latest.status}</strong>
          <span className="ml-2">Reference {latest.transactionReference}</span>
          {latest.reviewNote && <p className="mt-1">Organiser note: {latest.reviewNote}</p>}
          {latest.fileUrl && <a href={latest.fileUrl} target="_blank" rel="noreferrer" className="mt-1 inline-block underline">View receipt</a>}
        </div>
      )}

      {!loading && !pending && (
        <form onSubmit={submit} className="space-y-2">
          <label className="block font-semibold">UPI transaction reference
            <input type="text" value={reference} onChange={e => setReference(e.target.value)}
              placeholder="Transaction ID / UTR" maxLength={100} required
              className="mt-1 block w-full rounded-lg border border-blue-200 bg-white px-3 py-2 text-gray-900" />
          </label>
          <label className="block font-semibold">Payment receipt image (JPEG, PNG, WebP; up to 5 MB)
            <input type="file" accept="image/jpeg,image/png,image/webp"
              onChange={e => setFile(e.target.files?.[0] || null)} required
              className="mt-1 block w-full text-xs" />
          </label>
          <label className="flex items-start gap-2 font-semibold text-blue-950">
            <input type="checkbox" checked={recipientConfirmed}
              onChange={event => setRecipientConfirmed(event.target.checked)} />
            I checked that the recipient of this payment is exactly {tournament.gpayUpiId}.
          </label>
          <button type="submit" disabled={submitting || !file || !recipientConfirmed}
            className="rounded-lg bg-blue-800 px-3 py-2 font-bold text-white disabled:opacity-50">
            {submitting ? 'Uploading…' : latest?.status === 'rejected' ? 'Submit a new proof' : 'Submit proof for review'}
          </button>
        </form>
      )}
      {message && <p role="status" className="font-semibold text-blue-900">{message}</p>}
      {error && <p role="alert" className="font-semibold text-red-700">{error}</p>}
    </div>
  );
};
