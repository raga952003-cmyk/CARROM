import React, { useState } from 'react';

export type PaymentActionDetails = {
  method: 'cash' | 'upi' | 'bank_transfer';
  reference: string;
  reason: string;
};

export const PaymentActionForm: React.FC<{
  kind: 'payment' | 'waiver' | 'refund';
  participant: string;
  amount?: number;
  onClose: () => void;
  onSubmit: (details: PaymentActionDetails) => Promise<void>;
}> = ({ kind, participant, amount, onClose, onSubmit }) => {
  const [method, setMethod] = useState<PaymentActionDetails['method']>('cash');
  const [reference, setReference] = useState('');
  const [reason, setReason] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const title = kind === 'waiver' ? 'Waive fee and approve entry' : kind === 'payment' ? 'Record received payment' : 'Record completed refund';
  return <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
    <form aria-label={title} className="bg-white rounded-2xl p-6 max-w-md w-full space-y-4" onSubmit={async e => {
      e.preventDefault();
      if (busy) return;
      setError('');
      if (kind !== 'waiver' && (reference.trim().length < 3 || reference.trim().length > 120)) {
        setError('Enter a reference between 3 and 120 characters.'); return;
      }
      if (kind === 'payment' && method !== 'cash' && !/^[A-Za-z0-9]{6,80}$/.test(reference.replace(/[^A-Za-z0-9]/g, ''))) {
        setError('Enter a bank or UPI reference with 6 to 80 letters or digits.'); return;
      }
      if (kind !== 'payment' && (reason.trim().length < 5 || reason.trim().length > 500)) {
        setError('Enter a reason between 5 and 500 characters.'); return;
      }
      if (kind !== 'waiver' && !confirmed) { setError('Confirm the actual transfer before recording it.'); return; }
      setBusy(true);
      try { await onSubmit({ method, reference: reference.trim(), reason: reason.trim() }); onClose(); }
      catch (err) { setError(err instanceof Error ? err.message : 'Could not save this action.'); }
      finally { setBusy(false); }
    }}>
      <h3 className="font-bold text-lg">{title}</h3>
      <p className="text-sm text-gray-600">{participant}{amount !== undefined ? ` · ₹${amount}` : ''}</p>
      {kind === 'payment' && <label className="block text-sm">Payment method
        <select className="block w-full border rounded-lg p-2" value={method} onChange={e => setMethod(e.target.value as PaymentActionDetails['method'])}>
          <option value="cash">Cash</option><option value="upi">UPI</option><option value="bank_transfer">Bank transfer</option>
        </select>
      </label>}
      {kind !== 'waiver' && <label className="block text-sm">Transaction or receipt reference
        <input className="block w-full border rounded-lg p-2" value={reference} onChange={e => setReference(e.target.value)} maxLength={120} required />
      </label>}
      {kind !== 'payment' && <label className="block text-sm">Reason
        <textarea className="block w-full border rounded-lg p-2" value={reason} onChange={e => setReason(e.target.value)} minLength={5} maxLength={500} required />
      </label>}
      {kind !== 'waiver' && <label className="flex gap-2 text-sm">
        <input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />
        {kind === 'payment' ? 'I verified this money was received. Saving approves the entry.' : 'I verified this money was already returned outside this app. Saving does not send money.'}
      </label>}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      <div className="flex justify-end gap-3">
        <button type="button" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="submit" disabled={busy} className="bg-emerald-800 text-white rounded-lg px-4 py-2 disabled:opacity-50">{busy ? 'Saving…' : kind === 'waiver' ? 'Waive and approve' : 'Save verified record'}</button>
      </div>
    </form>
  </div>;
};
