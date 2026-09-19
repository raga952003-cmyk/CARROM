import React, { useState, useEffect } from 'react';
import {
  X,
  Check,
  Users,
  CreditCard,
  UserCheck,
  AlertTriangle,
  Loader2,
  ShieldCheck,
  Banknote
} from 'lucide-react';
import confetti from 'canvas-confetti';
import { Tournament, Player, Team, Registration } from '../../types/tournament';
import { useTournament } from '../../context/TournamentContext';
import { paymentService, PaymentDismissedError, PaymentUnconfirmedError } from '../../services/paymentService';

interface RegistrationFormModalProps {
  tournament: Tournament;
  isOpen: boolean;
  onClose: () => void;
}

/**
 * Enter a tournament, and pay for it.
 *
 * Two steps, not one, and the split is deliberate: the entry is saved BEFORE
 * checkout opens. A player who abandons payment, loses their connection or
 * closes the tab has a real pending registration waiting for them rather than
 * nothing at all, and the organiser can see them in the list and chase or
 * waive it. The alternative -- hold the form until the money clears -- loses
 * the entry every time a payment does not complete, which is often.
 *
 * Nothing here decides that an entry is paid. `payForRegistration` resolves
 * only once the server has verified Razorpay's signature and said so.
 */
export const RegistrationFormModal: React.FC<RegistrationFormModalProps> = ({
  tournament,
  isOpen,
  onClose
}) => {
  const { registerForTournament, currentUser, refreshTournaments } = useTournament();

  const [regType, setRegType] = useState<'singles' | 'doubles'>(
    tournament.category === 'doubles' ? 'doubles' : 'singles'
  );

  // An admin registering on someone's behalf has no player profile, so the
  // player-only fields are read from the signed-in user only when they are one.
  const asPlayer = currentUser && currentUser.role === 'player' ? (currentUser as Player) : null;

  // Singles Fields
  const [playerName, setPlayerName] = useState(currentUser?.name || '');
  const [phone, setPhone] = useState(asPlayer?.phone || '');
  const [email, setEmail] = useState(currentUser?.email || '');
  const [club, setClub] = useState(asPlayer?.club || '');
  const [city, setCity] = useState(asPlayer?.city || tournament.city || '');

  // Doubles Team Fields. These start empty on purpose: pre-filled sample values
  // were being submitted verbatim, registering a fictitious partner.
  const [teamName, setTeamName] = useState('');
  const [partnerName, setPartnerName] = useState('');
  const [partnerPhone, setPartnerPhone] = useState('');
  const [partnerEmail, setPartnerEmail] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const [step, setStep] = useState<'form' | 'payment' | 'done'>('form');
  const [registration, setRegistration] = useState<Registration | null>(null);
  const [isPaying, setIsPaying] = useState(false);
  const [paymentError, setPaymentError] = useState('');
  // Money left the account but the server never confirmed it. Tracked apart
  // from paymentError because the correct advice inverts: offering "Pay"
  // here invites a second charge for the same entry.
  const [unconfirmed, setUnconfirmed] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');

  // Whether this server can take money at all. Null while unknown, so the
  // form does not promise an online payment before the answer arrives, and
  // does not refuse one either.
  const [paymentsEnabled, setPaymentsEnabled] = useState<boolean | null>(null);

  const fee = Number(tournament.entryFee) || 0;
  const hasFee = fee > 0;

  useEffect(() => {
    if (!isOpen || !hasFee) return;
    let cancelled = false;
    paymentService
      .getConfig()
      .then(config => { if (!cancelled) setPaymentsEnabled(!!config.enabled); })
      // A config call that fails is not worth an error on the form: fall back
      // to the pay-at-venue wording, which is what the app did before online
      // payment existed and is always a truthful thing to say.
      .catch(() => { if (!cancelled) setPaymentsEnabled(false); });
    return () => { cancelled = true; };
  }, [isOpen, hasFee]);

  if (!isOpen) return null;

  const celebrate = () => {
    try {
      confetti({ particleCount: 80, spread: 60, origin: { y: 0.6 } });
    } catch (err) {}
  };

  /**
   * Take the entry fee for an entry that already exists.
   *
   * Called straight after registering, and again from the Pay button when the
   * first attempt was abandoned or refused.
   */
  const startPayment = async (registrationId: string) => {
    setIsPaying(true);
    setPaymentError('');
    try {
      const { registration: confirmed } = await paymentService.payForRegistration(registrationId);
      setRegistration(confirmed);
      setStep('done');
      celebrate();
      // The entry is approved now, so the dashboard and the organiser's list
      // are both stale.
      refreshTournaments();
    } catch (e: any) {
      if (e instanceof PaymentDismissedError || e?.dismissed) {
        // They closed the window. Not an error -- the entry is saved and
        // waiting, and the panel already says so.
        setPaymentError('');
      } else if (e instanceof PaymentUnconfirmedError || e?.paid) {
        // The charge went through; only our confirmation of it did not. The
        // webhook will settle it, so the one thing this screen must not do is
        // invite them to pay again.
        setUnconfirmed(true);
        setPaymentError(e?.message || '');
      } else {
        setPaymentError(e?.message || 'The payment could not be completed. Please try again.');
      }
      setStep('payment');
    } finally {
      setIsPaying(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg('');

    if (regType === 'doubles' && !partnerName.trim()) {
      setErrorMsg('Enter your partner name to register a doubles team.');
      return;
    }

    setIsSubmitting(true);
    try {
      const self: Player = {
        id: currentUser?.id || '',
        name: playerName,
        phone,
        email,
        club,
        city,
        rating: asPlayer?.rating || 1500
      };

      let created: Registration;
      if (regType === 'singles') {
        created = await registerForTournament(tournament.id, 'singles', self);
      } else {
        const partner: Player = {
          // Empty id: this partner has no account yet, so the backend creates
          // their profile from these details.
          id: '',
          name: partnerName.trim(),
          phone: partnerPhone || undefined,
          email: partnerEmail || undefined,
          club,
          city
        };
        const newTeam: Team = {
          id: '',
          name: teamName.trim() || (playerName + ' & ' + partnerName.trim()),
          player1: self,
          player2: partner,
          club,
          city
        };
        created = await registerForTournament(tournament.id, 'doubles', newTeam);
      }

      setRegistration(created);

      // The server decides whether anything is owed -- it may have waived the
      // fee, or the organiser may have entered this player themselves. Only a
      // registration it left as 'pending' payment needs checkout.
      const owesMoney = created?.paymentStatus === 'pending';

      if (owesMoney && paymentsEnabled && created?.id) {
        setStep('payment');
        await startPayment(created.id);
      } else {
        setStep('done');
        celebrate();
      }
    } catch (error: any) {
      setErrorMsg(
        error?.message ||
          'Failed to register. You might already be registered in this tournament.'
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const isPaid = registration?.paymentStatus === 'paid';
  const isWaived = registration?.paymentStatus === 'waived';

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-black/60 backdrop-blur-xs flex items-start sm:items-center justify-center p-2 sm:p-4 animate-in fade-in duration-150">
      <div className="relative bg-white rounded-2xl sm:rounded-3xl max-w-lg w-full p-4 sm:p-6 shadow-2xl border border-gray-100 overflow-hidden">

        {/* Close Button */}
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-gray-400 hover:text-gray-600 p-1.5 rounded-lg hover:bg-gray-100 transition-colors"
        >
          <X className="w-5 h-5" />
        </button>

        {step === 'payment' ? (
          /* ---------------------------------------------------------------
           * Entry saved, fee outstanding.
           *
           * Reached when checkout was closed or refused. The entry exists and
           * is safe; this screen exists so that is unmistakable, because a
           * player who thinks their entry vanished will register again.
           * --------------------------------------------------------------- */
          <div className="py-4 space-y-4">
            <div className="text-center space-y-2">
              <div className="w-16 h-16 rounded-full bg-amber-100 flex items-center justify-center mx-auto shadow-inner">
                {isPaying
                  ? <Loader2 className="w-8 h-8 text-amber-600 animate-spin" />
                  : <CreditCard className="w-8 h-8 text-amber-600" />}
              </div>
              <div>
                <span className="text-[10px] font-black text-amber-900 uppercase tracking-widest bg-amber-100 px-3 py-1 rounded-full">
                  Payment Pending
                </span>
                <h3 className="font-serif font-bold text-2xl text-gray-900 mt-2">
                  {isPaying
                    ? 'Waiting for payment…'
                    : unconfirmed ? 'Payment received — confirming' : 'Your entry is saved'}
                </h3>
                <p className="text-xs text-gray-500 mt-1 max-w-sm mx-auto">
                  {isPaying
                    ? 'Complete the payment in the Razorpay window. Do not close this page.'
                    : unconfirmed
                      ? <>Your payment for <strong>{tournament.name}</strong> went through. We
                         could not record the confirmation just now, but it completes on its
                         own — <strong>do not pay again</strong>. Contact the organisers if
                         your entry still shows as unpaid in a few minutes.</>
                      : <>Your place in <strong>{tournament.name}</strong> is held but not
                         confirmed. It is confirmed the moment the entry fee is paid.</>}
                </p>
              </div>
            </div>

            {paymentError && !unconfirmed && (
              <div className="p-3 bg-red-50 text-red-800 text-xs font-semibold rounded-xl border border-red-200 flex items-start gap-2">
                <AlertTriangle className="w-4.5 h-4.5 text-red-600 shrink-0 mt-px" />
                <span>{paymentError}</span>
              </div>
            )}

            <div className="bg-gray-50 p-4 rounded-2xl border border-gray-200 text-left text-xs space-y-2">
              <div className="flex justify-between">
                <span className="text-gray-500">Participant:</span>
                <strong className="text-gray-900">{regType === 'singles' ? playerName : teamName}</strong>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">Entry Fee Due:</span>
                <strong className="text-amber-700">₹{fee.toLocaleString('en-IN')}</strong>
              </div>
            </div>

            <div className="flex items-center justify-end gap-3 pt-1">
              <button
                type="button"
                onClick={onClose}
                disabled={isPaying}
                className="px-4 py-2.5 text-xs font-semibold text-gray-600 hover:bg-gray-100 rounded-xl disabled:opacity-50"
              >
                {unconfirmed ? 'Close' : 'Pay Later'}
              </button>
              {!unconfirmed && (
              <button
                type="button"
                onClick={() => registration?.id && startPayment(registration.id)}
                disabled={isPaying || !registration?.id}
                className="px-5 py-2.5 bg-[#0B5D3B] hover:bg-[#08472d] text-white text-xs font-bold rounded-xl shadow-md flex items-center gap-1.5 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isPaying
                  ? <Loader2 className="w-4 h-4 animate-spin" />
                  : <CreditCard className="w-4 h-4 text-[#D4A72C]" />}
                <span>{isPaying ? 'Processing…' : `Pay ₹${fee.toLocaleString('en-IN')}`}</span>
              </button>
              )}
            </div>

            <p className="text-[10px] text-center text-gray-400">
              You can close this and pay later from your dashboard. Your entry will
              not be included in the draw until the fee is paid.
            </p>
          </div>
        ) : step === 'done' ? (
          /* Success Screen */
          <div className="text-center py-6 space-y-4">
            <div className="w-16 h-16 rounded-full bg-emerald-100 text-[#0B5D3B] flex items-center justify-center mx-auto shadow-inner">
              <UserCheck className="w-8 h-8 text-emerald-600" />
            </div>

            <div>
              <span className="text-[10px] font-black text-[#D4A72C] uppercase tracking-widest bg-emerald-950 px-3 py-1 rounded-full">
                {isPaid ? 'Entry Confirmed' : 'Registration Received'}
              </span>
              <h3 className="font-serif font-bold text-2xl text-gray-900 mt-2">
                {isPaid ? "You're in the Tournament!" : 'Entry submitted'}
              </h3>
              <p className="text-xs text-gray-500 mt-1 max-w-sm mx-auto">
                {isPaid ? (
                  <>Your entry for <strong>{tournament.name}</strong> is paid and confirmed.
                    You'll receive real-time schedule alerts once boards are assigned.</>
                ) : (
                  <>Your entry for <strong>{tournament.name}</strong> has been registered
                    and is awaiting the organiser's approval.</>
                )}
              </p>
            </div>

            <div className="bg-gray-50 p-4 rounded-2xl border border-gray-200 text-left text-xs space-y-2">
              <div className="flex justify-between">
                <span className="text-gray-500">Participant:</span>
                <strong className="text-gray-900">{regType === 'singles' ? playerName : teamName}</strong>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">Event Category:</span>
                <strong className="text-gray-900 capitalize">{regType}</strong>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">Entry Fee:</span>
                {/* Says what actually happened. This used to read "Verified" on
                    every entry, including ones where no money had changed hands. */}
                {isPaid ? (
                  <strong className="text-emerald-700">
                    ₹{fee.toLocaleString('en-IN')} · Paid
                  </strong>
                ) : isWaived ? (
                  <strong className="text-gray-900">{hasFee ? 'Waived' : 'Free entry'}</strong>
                ) : (
                  <strong className="text-amber-700">
                    ₹{fee.toLocaleString('en-IN')} · Due at venue
                  </strong>
                )}
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">Tournament Dates:</span>
                <strong className="text-gray-900">{tournament.tournamentStartDate} to {tournament.tournamentEndDate}</strong>
              </div>
            </div>

            <button
              onClick={onClose}
              className="w-full py-3 bg-[#0B5D3B] hover:bg-[#08472d] text-white font-bold text-xs rounded-xl shadow-md transition-all"
            >
              View My Tournament Dashboard
            </button>
          </div>
        ) : (
          /* Registration Form */
          <form onSubmit={handleSubmit} className="space-y-4">

            <div>
              <span className="text-[10px] font-bold text-[#0B5D3B] uppercase tracking-wider bg-emerald-50 px-2.5 py-0.5 rounded-full border border-emerald-200">
                Official Entry Registration
              </span>
              <h3 className="font-serif font-bold text-xl text-gray-900 mt-1">
                Register for {tournament.name}
              </h3>
              <p className="text-xs text-gray-500">
                Entry Fee: <strong>₹{fee.toLocaleString('en-IN')}</strong> · Deadline: <strong>{tournament.registrationEndDate}</strong>
              </p>
            </div>

            {errorMsg && (
              <div className="p-3 bg-red-50 text-red-800 text-xs font-semibold rounded-xl border border-red-200 flex items-center gap-2">
                <AlertTriangle className="w-4.5 h-4.5 text-red-600 shrink-0" />
                <span>{errorMsg}</span>
              </div>
            )}

            {/* Category Toggle (if tournament supports both) */}
            {tournament.category === 'both' && (
              <div>
                <label className="block text-xs font-bold text-gray-700 mb-1">
                  Select Event Format
                </label>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => setRegType('singles')}
                    className={`py-2 text-xs font-bold rounded-xl border transition-all ${
                      regType === 'singles'
                        ? 'bg-[#0B5D3B] text-white border-[#0B5D3B] shadow-xs'
                        : 'bg-gray-50 text-gray-700 border-gray-200 hover:bg-gray-100'
                    }`}
                  >
                    Singles Championship
                  </button>

                  <button
                    type="button"
                    onClick={() => setRegType('doubles')}
                    className={`py-2 text-xs font-bold rounded-xl border transition-all ${
                      regType === 'doubles'
                        ? 'bg-[#0B5D3B] text-white border-[#0B5D3B] shadow-xs'
                        : 'bg-gray-50 text-gray-700 border-gray-200 hover:bg-gray-100'
                    }`}
                  >
                    Doubles Team
                  </button>
                </div>
              </div>
            )}

            {/* Participant Details */}
            <div className="space-y-3 pt-1 text-xs">

              {regType === 'doubles' && (
                <div>
                  <label className="block font-bold text-gray-700 mb-1">Team Name *</label>
                  <input
                    type="text"
                    value={teamName}
                    onChange={e => setTeamName(e.target.value)}
                    required
                    className="w-full p-2.5 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                    placeholder="e.g. Striker Kings"
                  />
                </div>
              )}

              <div>
                <label className="block font-bold text-gray-700 mb-1">
                  {regType === 'doubles' ? 'Captain / Player 1 Name *' : 'Full Name *'}
                </label>
                <input
                  type="text"
                  value={playerName}
                  onChange={e => setPlayerName(e.target.value)}
                  required
                  className="w-full p-2.5 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                  placeholder="Player full name"
                />
              </div>

              {regType === 'doubles' && (
                <div className="p-3 bg-blue-50/60 rounded-xl border border-blue-200 space-y-2">
                  <div className="font-bold text-blue-950 text-xs flex items-center gap-1">
                    <Users className="w-3.5 h-3.5 text-blue-700" />
                    <span>Player 2 (Doubles Partner)</span>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <input
                      type="text"
                      value={partnerName}
                      onChange={e => setPartnerName(e.target.value)}
                      required
                      className="w-full p-2 border border-gray-200 rounded-lg bg-white"
                      placeholder="Partner Name"
                    />
                    <input
                      type="tel"
                      value={partnerPhone}
                      onChange={e => setPartnerPhone(e.target.value)}
                      className="w-full p-2 border border-gray-200 rounded-lg bg-white"
                      placeholder="Partner Phone"
                    />
                  </div>
                  <input
                    type="email"
                    value={partnerEmail}
                    onChange={e => setPartnerEmail(e.target.value)}
                    className="w-full p-2 border border-gray-200 rounded-lg bg-white"
                    placeholder="Partner Email (optional)"
                  />
                  <p className="text-[10px] text-blue-800/80">
                    If your partner already has an account, enter their email so their
                    existing profile is used instead of creating a duplicate.
                  </p>
                </div>
              )}

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block font-bold text-gray-700 mb-1">Mobile Contact</label>
                  <input
                    type="tel"
                    value={phone}
                    onChange={e => setPhone(e.target.value)}
                    required
                    className="w-full p-2.5 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                  />
                </div>

                <div>
                  <label className="block font-bold text-gray-700 mb-1">Email</label>
                  <input
                    type="email"
                    value={email}
                    onChange={e => setEmail(e.target.value)}
                    required
                    className="w-full p-2.5 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block font-bold text-gray-700 mb-1">Carrom Club / Academy</label>
                  <input
                    type="text"
                    value={club}
                    onChange={e => setClub(e.target.value)}
                    className="w-full p-2.5 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                    placeholder="e.g. Deccan Gymkhana"
                  />
                </div>

                <div>
                  <label className="block font-bold text-gray-700 mb-1">City</label>
                  <input
                    type="text"
                    value={city}
                    onChange={e => setCity(e.target.value)}
                    className="w-full p-2.5 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                    placeholder="City"
                  />
                </div>
              </div>

            </div>

            {/* What happens to the money, stated before they commit to it. */}
            {hasFee && (
              <div className="p-3 bg-emerald-50 rounded-xl border border-emerald-200 flex items-center justify-between text-xs gap-2">
                <div className="flex items-center space-x-2 text-emerald-900">
                  {paymentsEnabled
                    ? <CreditCard className="w-4 h-4 text-emerald-700 shrink-0" />
                    : <Banknote className="w-4 h-4 text-emerald-700 shrink-0" />}
                  <span>Entry Fee: <strong>₹{fee.toLocaleString('en-IN')}</strong></span>
                </div>
                <span className="text-[10px] font-bold text-emerald-800 bg-white px-2 py-0.5 rounded border border-emerald-200 text-right">
                  {paymentsEnabled === null
                    ? 'Checking…'
                    : paymentsEnabled
                      ? 'Pay now to confirm'
                      : 'Payable at venue'}
                </span>
              </div>
            )}

            {hasFee && paymentsEnabled && (
              <p className="text-[10px] text-gray-500 flex items-start gap-1.5">
                <ShieldCheck className="w-3.5 h-3.5 text-emerald-600 shrink-0 mt-px" />
                <span>
                  Your entry is saved first, then the payment window opens. Your place is
                  confirmed once the fee is paid — you can also pay later from your dashboard.
                </span>
              </p>
            )}

            {/* Actions */}
            <div className="pt-2 flex items-center justify-end space-x-3">
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-2.5 text-xs font-semibold text-gray-600 hover:bg-gray-100 rounded-xl"
              >
                Cancel
              </button>

              <button
                type="submit"
                disabled={isSubmitting}
                className="px-5 py-2.5 bg-[#0B5D3B] hover:bg-[#08472d] text-white text-xs font-bold rounded-xl shadow-md flex items-center gap-1.5 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isSubmitting
                  ? <Loader2 className="w-4 h-4 animate-spin" />
                  : <Check className="w-4 h-4 text-[#D4A72C]" />}
                <span>
                  {isSubmitting
                    ? 'Submitting...'
                    : hasFee && paymentsEnabled
                      ? `Continue to Pay ₹${fee.toLocaleString('en-IN')}`
                      : 'Confirm & Submit Entry'}
                </span>
              </button>
            </div>

          </form>
        )}

      </div>
    </div>
  );
};
