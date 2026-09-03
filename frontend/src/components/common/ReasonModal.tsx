import React, { useEffect, useState } from 'react';
import { AlertTriangle, Loader2, MessageSquareText, X } from 'lucide-react';

/**
 * Ask for a reason, and refuse to go on without one.
 *
 * Several match-day decisions are rulings rather than scores — awarding a level
 * match, correcting a board, reopening a confirmed result — and the reason is
 * the only part of them that can be read back later. They used to be collected
 * with window.prompt, which cannot be disabled while the write is in flight,
 * cannot show why the server said no, and on a phone is a system dialog with a
 * one-line box. So: a small modal that holds the text, keeps Confirm disabled
 * while the box is empty or the caller is busy, and shows the caller's error
 * under the box.
 *
 * The modal never closes itself. The caller closes it once the write has
 * succeeded, so a refusal leaves the organiser looking at their own words and
 * the server's answer, not at a closed dialog and a toast.
 */

interface ReasonModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** Receives the trimmed reason. Close the modal yourself once the write succeeds. */
  onConfirm: (reason: string) => void | Promise<void>;
  title: string;
  description: string;
  placeholder?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** True while the caller's write is in flight: the box and both buttons are disabled. */
  busy?: boolean;
  /** Why the last attempt failed, shown under the box. */
  error?: string;
  variant?: 'danger' | 'warning' | 'primary';
}

export const ReasonModal: React.FC<ReasonModalProps> = ({
  isOpen,
  onClose,
  onConfirm,
  title,
  description,
  placeholder = 'Say why — it is recorded with the result.',
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  busy = false,
  error = '',
  variant = 'primary'
}) => {
  const [reason, setReason] = useState('');

  // Every opening starts with an empty box. The modal stays mounted between
  // uses, so without this the previous ruling's words would still be sitting
  // there, and Confirm would already be enabled.
  useEffect(() => {
    if (isOpen) setReason('');
  }, [isOpen]);

  if (!isOpen) return null;

  const trimmed = reason.trim();
  const canConfirm = !!trimmed && !busy;

  const getVariantStyles = () => {
    switch (variant) {
      case 'danger':
        return {
          icon: <AlertTriangle className="w-6 h-6 text-red-600" />,
          iconBg: 'bg-red-100',
          btnBg: 'bg-red-600 hover:bg-red-700 text-white shadow-red-200'
        };
      case 'warning':
        return {
          icon: <AlertTriangle className="w-6 h-6 text-amber-600" />,
          iconBg: 'bg-amber-100',
          btnBg: 'bg-amber-600 hover:bg-amber-700 text-white shadow-amber-200'
        };
      default:
        return {
          icon: <MessageSquareText className="w-6 h-6 text-[#0B5D3B]" />,
          iconBg: 'bg-emerald-100',
          btnBg: 'bg-[#0B5D3B] hover:bg-[#08442b] text-white shadow-emerald-200'
        };
    }
  };

  const styles = getVariantStyles();

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (canConfirm) onConfirm(trimmed);
  };

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-black/50 backdrop-blur-xs flex items-center justify-center p-4 animate-in fade-in duration-150">
      <div className="relative bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-100 transform animate-in zoom-in-95 duration-150">
        <button
          type="button"
          onClick={onClose}
          disabled={busy}
          aria-label="Close"
          className="absolute top-4 right-4 text-gray-600 hover:text-gray-600 p-1 rounded-lg hover:bg-gray-100 transition-colors disabled:opacity-40"
        >
          <X className="w-5 h-5" />
        </button>

        <div className="flex items-start space-x-4">
          <div className={`p-3 rounded-xl shrink-0 ${styles.iconBg}`}>
            {styles.icon}
          </div>
          <div className="flex-1">
            <h3 className="text-lg font-bold text-gray-900 leading-tight mb-1.5">
              {title}
            </h3>
            <p className="text-xs text-gray-600 leading-relaxed">
              {description}
            </p>
          </div>
        </div>

        <form onSubmit={submit}>
          <label className="block mt-4 mb-1 text-xs font-bold text-gray-700">
            Reason *
          </label>
          <textarea
            autoFocus
            rows={3}
            value={reason}
            onChange={e => setReason(e.target.value)}
            disabled={busy}
            placeholder={placeholder}
            className="w-full p-2.5 text-xs border border-gray-200 rounded-xl bg-white focus:ring-2 focus:ring-[#0B5D3B] focus:outline-hidden resize-none disabled:opacity-60"
          />

          {error && (
            <div role="alert" className="mt-2 p-2.5 rounded-xl bg-red-50 border border-red-200 text-xs text-red-800 flex items-start gap-1.5">
              <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0 text-red-600" />
              <span className="flex-1 leading-snug">{error}</span>
            </div>
          )}

          <div className="mt-5 flex items-center justify-end space-x-3">
            <button
              type="button"
              onClick={onClose}
              disabled={busy}
              className="px-4 py-2 text-xs font-semibold text-gray-700 hover:bg-gray-100 rounded-xl transition-colors disabled:opacity-40"
            >
              {cancelLabel}
            </button>
            <button
              type="submit"
              disabled={!canConfirm}
              className={`px-4 py-2 text-xs font-bold rounded-xl shadow-md transition-all flex items-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed ${styles.btnBg}`}
            >
              {busy && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              <span>{confirmLabel}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
