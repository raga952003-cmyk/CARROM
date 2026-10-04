import { useHashRoute } from '../../utils/useHashRoute';
import { TournamentPoster } from '../common/TournamentPoster';
import { usePosterQr } from '../common/usePosterQr';
import { scoringSummary } from '../../utils/posterFacts';
import React, { useState } from 'react';
import { findMyMatches, opponentOf } from '../../utils/myMatches';
import { groupMatches, resultSummary, outcomeFor, finishedIsProvisional, MatchGroupKey } from '../../utils/matchGroups';
import { paymentService, PaymentDismissedError, PaymentNotStartedError, PaymentUnconfirmedError } from '../../services/paymentService';
import { 
  Trophy, 
  Calendar, 
  MapPin, 
  Users, 
  Search, 
  Filter, 
  ArrowRight, 
  Clock, 
  Flame, 
  CheckCircle2, 
  Sparkles, 
  UserCheck, 
  QrCode, 
  Palette, 
  Award,
  ChevronRight,
  Radio,
  CreditCard,
  Loader2
} from 'lucide-react';
import { Tournament, Match } from '../../types/tournament';
import { useTournament } from '../../context/TournamentContext';
import { RegistrationFormModal } from './RegistrationFormModal';
import { FixtureScheduleView } from '../admin/FixtureScheduleView';
import { LiveMatchController } from '../admin/LiveMatchController';
import { StandingsSections } from '../common/StandingsSections';
import { NextMatchCard } from './NextMatchCard';
import { GPayPaymentProof } from './GPayPaymentProof';
import { KnockoutBracketView } from '../common/KnockoutBracketView';
import { isRegistrationDeadlinePassed } from '../../utils/registrationDeadline';
import { validTournamentUpiDestination } from '../../utils/upiDestination';

export const PlayerDashboard: React.FC = () => {
  const { 
    tournaments, 
    activeTournamentId, 
    setActiveTournamentId,
    activeMatch,
    setActiveMatch,
    currentUser,
    refreshTournaments
  } = useTournament();

  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'registration_open' | 'ongoing' | 'completed'>('all');
  const [categoryFilter, setCategoryFilter] = useState<'all' | 'singles' | 'doubles'>('all');

  const [isRegisterModalOpen, setIsRegisterModalOpen] = useState(false);
  const [selectedTournamentForReg, setSelectedTournamentForReg] = useState<Tournament | null>(null);
  const [clockNow, setClockNow] = useState(() => Date.now());

  React.useEffect(() => {
    const timer = window.setInterval(() => setClockNow(Date.now()), 15_000);
    return () => window.clearInterval(timer);
  }, []);

  // Sub-view in active tournament
  const [activeTab, setActiveTab] = useState<'my_matches' | 'schedule' | 'standings' | 'knockout' | 'poster'>('my_matches');

  const currentTournament = tournaments.find(t => t.id === activeTournamentId) || tournaments[0];
  const { qr: posterQr } = usePosterQr(currentTournament?.id || '');
  const receivingUpi = validTournamentUpiDestination(currentTournament?.gpayUpiId);

  // Check if current user is registered in current tournament
  const userRegistration = currentTournament?.registrations?.find(r => 
    (currentUser?.id && r.player?.id === currentUser.id) || 
    (currentUser?.id && r.team?.player1?.id === currentUser.id) ||
    (currentUser?.id && r.team?.player2?.id === currentUser.id)
  );

  const joinRoute = useHashRoute();
  const openedJoin = React.useRef('');
  React.useEffect(() => {
    const id = joinRoute.view === 'join' ? joinRoute.segments[1] : '';
    if (!id || id !== currentTournament?.id || openedJoin.current === id) return;
    openedJoin.current = id;
    if ((!userRegistration || userRegistration.status === 'rejected') && currentTournament.status === 'registration_open' && !isRegistrationDeadlinePassed(currentTournament.registrationEndDate)) {
      setSelectedTournamentForReg(currentTournament);
      setIsRegisterModalOpen(true);
    }
  }, [joinRoute.view, joinRoute.segments[1], currentTournament, userRegistration]);

  // Matches linked to this account or to a doubles team containing it.
  const myMatches = React.useMemo(
    () => findMyMatches(currentTournament, currentUser),
    [currentTournament, currentUser]
  );

  // My matches, split three ways.
  //
  // Shared with the public board (utils/matchGroups) so the two screens cannot
  // disagree about what counts as live -- a paused match is being played, and
  // a match finished but not yet confirmed is over, not upcoming.
  const mine = React.useMemo(() => groupMatches(myMatches), [myMatches]);

  const [mineGroup, setMineGroup] = useState<MatchGroupKey>('live');

  // Settling an entry fee that was left unpaid.
  //
  // The registration form tells the player "you can pay later from your
  // dashboard", and until now that was not true: closing the modal unmounted
  // the only component that knew the registration id, so an abandoned payment
  // had no route back and the entry sat pending until an organiser noticed.
  // Saving the entry before opening checkout is only worth doing if there IS a
  // way back to it.
  const [payingFee, setPayingFee] = useState(false);
  const [feeError, setFeeError] = useState('');
  const [paymentPendingConfirmation, setPaymentPendingConfirmation] = useState<string | null>(null);
  const [onlinePaymentsAvailable, setOnlinePaymentsAvailable] = useState(false);
  const [razorpayFailedForEntry, setRazorpayFailedForEntry] = useState<string | null>(null);
  const [gpayProofPendingByEntry, setGpayProofPendingByEntry] = useState<Record<string, boolean | null>>({});
  const gpayProofPending = userRegistration?.id
    ? gpayProofPendingByEntry[userRegistration.id] : undefined;
  const checkoutAvailableForEntry = onlinePaymentsAvailable &&
    razorpayFailedForEntry !== userRegistration?.id;
  const paymentInstructions = !userRegistration || !currentTournament
    ? ''
    : paymentPendingConfirmation === userRegistration.id
      ? 'Payment confirmation is pending. Do not pay again; contact the organiser if it does not update.'
      : gpayProofPending === true
        ? 'A payment or receipt is already recorded for this entry. Do not pay again.'
        : receivingUpi && gpayProofPending !== false
          ? 'Checking earlier payments and receipts before offering another payment.'
          : checkoutAvailableForEntry && receivingUpi
            ? 'Choose one: Razorpay below, or direct UPI with a receipt in the section below. Do not pay twice.'
            : checkoutAvailableForEntry
              ? 'Razorpay confirms a successful payment automatically.'
              : receivingUpi
                ? 'Razorpay checkout is unavailable. Use the exact tournament UPI ID below and upload the receipt for organiser review.'
                : 'Contact the organiser to settle the entry fee.';

  React.useEffect(() => {
    paymentService.getConfig()
      .then(config => setOnlinePaymentsAvailable(config.enabled))
      .catch(() => setOnlinePaymentsAvailable(false));
  }, []);

  React.useEffect(() => {
    if (userRegistration?.id === paymentPendingConfirmation &&
        userRegistration.paymentStatus !== 'pending') {
      setPaymentPendingConfirmation(null);
      setFeeError('');
    }
  }, [paymentPendingConfirmation, userRegistration?.id, userRegistration?.paymentStatus]);

  const settleEntryFee = async (registrationId: string) => {
    setPayingFee(true);
    setFeeError('');
    try {
      await paymentService.payForRegistration(registrationId);
      // Checkout has already been verified. A later dashboard refresh failure
      // must never be mistaken for a payment failure and offer a second fee.
      setPaymentPendingConfirmation(registrationId);
      try {
        await refreshTournaments();
      } catch {
        setFeeError('Your payment was verified, but the dashboard could not refresh. Do not pay again; reload the page or contact the organiser.');
      }
    } catch (e: any) {
      // A dismissed window is not a failure worth reporting -- they changed
      // their mind and the entry is exactly as it was.
      if (e instanceof PaymentUnconfirmedError || e?.paid) {
        setPaymentPendingConfirmation(registrationId);
        setFeeError(e?.message || 'Payment received. Please wait for confirmation; do not pay again.');
        try {
          await refreshTournaments();
        } catch {
          // The charge still needs review even if the page cannot refresh.
        }
      } else if (!(e instanceof PaymentDismissedError || e?.dismissed)) {
        setRazorpayFailedForEntry(registrationId);
        setFeeError(e instanceof PaymentNotStartedError && e.requiresSignIn
          ? 'Your sign-in could not be verified. Sign in again before making a payment. If an earlier attempt was debited, do not pay again; contact the organiser.'
          : e instanceof PaymentNotStartedError
            ? receivingUpi
              ? 'Razorpay checkout could not start. You can use the exact tournament UPI ID below after checking that no earlier attempt debited your account. Upload the receipt for organiser review.'
              : 'Razorpay checkout could not start. Contact the organiser to arrange payment.'
            : receivingUpi
              ? 'Razorpay could not complete this attempt. Check your bank account first. If any amount was debited, do not pay again; contact the organiser. Otherwise, use the exact tournament UPI ID below and upload your receipt for review.'
              : 'Razorpay could not complete this attempt. If any amount was debited, do not pay again. Contact the organiser for help.');
      }
    } finally {
      setPayingFee(false);
    }
  };
  // Land on a group that has something in it, but stop moving once the player
  // has picked one themselves -- otherwise the tab jumps out from under them
  // the moment their live match ends.
  const touchedMineGroup = React.useRef(false);
  React.useEffect(() => {
    if (touchedMineGroup.current) return;
    if (mine.live.length) setMineGroup('live');
    else if (mine.upcoming.length) setMineGroup('upcoming');
    else if (mine.finished.length) setMineGroup('finished');
  }, [mine.live.length, mine.upcoming.length, mine.finished.length]);

  const MINE_GROUPS: { key: MatchGroupKey; label: string; rows: Match[] }[] = [
    { key: 'live', label: 'Live', rows: mine.live },
    { key: 'upcoming', label: 'Upcoming', rows: mine.upcoming },
    { key: 'finished', label: 'Finished', rows: mine.finished },
  ];
  const shownMine = MINE_GROUPS.find(g => g.key === mineGroup) || MINE_GROUPS[0];

  // Next upcoming match. Live first -- a match in play is more urgent than the
  // one after it -- then the earliest still to come.
  const nextMatch = mine.live[0] || mine.upcoming[0];

  // Filter tournaments for discovery
  const filteredTournaments = tournaments.filter(t => {
    const matchesSearch = t.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
                          t.city.toLowerCase().includes(searchTerm.toLowerCase()) ||
                          t.venue.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesStatus = statusFilter === 'all' || t.status === statusFilter;
    const matchesCat = categoryFilter === 'all' || t.category === categoryFilter || t.category === 'both';
    return matchesSearch && matchesStatus && matchesCat;
  });

  const handleOpenRegistration = (t: Tournament, e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    if (t.status !== 'registration_open' || isRegistrationDeadlinePassed(t.registrationEndDate)) return;
    setSelectedTournamentForReg(t);
    setIsRegisterModalOpen(true);
  };

  return (
    <div id="player-dashboard-root" className="space-y-6">
      
      {/* If spectator is viewing a live match */}
      {activeMatch && currentTournament ? (
        <LiveMatchController
          tournament={currentTournament}
          match={activeMatch}
          onBack={() => setActiveMatch(null)}
        />
      ) : (
        <>
          {/* Personalized Player Hero / Next Match Banner */}
          {nextMatch && currentTournament ? (
            <div className="bg-gradient-to-r from-[#0B5D3B] via-[#094e32] to-[#124230] text-white rounded-3xl p-6 shadow-xl border border-emerald-600/40 relative overflow-hidden">
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 relative z-10">
                <div>
                  <div className="flex items-center space-x-2">
                    <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-[#D4A72C] text-[#202522] uppercase tracking-wider">
                      <Flame className="w-3 h-3" />
                      {nextMatch.status === 'live' ? 'Live Match In Progress' : 'Your Next Scheduled Match'}
                    </span>
                    <span className="text-xs text-emerald-200">
                      {currentTournament.name}
                    </span>
                  </div>

                  <h2 className="text-xl sm:text-2xl font-serif font-bold text-white mt-1.5">
                    Match #{nextMatch.matchNumber} · Board #{nextMatch.boardNumber}
                  </h2>
                  <p className="text-xs sm:text-sm text-emerald-100 mt-0.5">
                    Opponent: <strong>{opponentOf(nextMatch, currentUser, currentTournament)}</strong> · Scheduled: {nextMatch.scheduledTime}
                  </p>
                </div>

                <div className="flex items-center space-x-3 self-start md:self-auto shrink-0">
                  <div className="bg-emerald-950/80 px-4 py-2 rounded-2xl border border-emerald-700/60 text-center">
                    <div className="text-[10px] text-emerald-300 uppercase font-bold">Assigned Board</div>
                    <div className="text-xl font-black text-[#D4A72C]">Board #{nextMatch.boardNumber}</div>
                  </div>

                  <button
                    onClick={() => setActiveMatch(nextMatch)}
                    className="px-5 py-3 bg-[#D4A72C] hover:bg-[#c29623] text-[#202522] font-bold text-xs rounded-2xl shadow-lg transition-all flex items-center gap-2"
                  >
                    <span>{nextMatch.status === 'live' ? 'Spectate Live Match' : 'View Match Details'}</span>
                    <ArrowRight className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          ) : (
            /* Welcome Player Banner */
            <div className="bg-gradient-to-r from-[#0B5D3B] to-[#124230] text-white rounded-3xl p-6 sm:p-7 shadow-lg border border-emerald-700/40 flex flex-col md:flex-row md:items-center justify-between gap-4">
              <div>
                <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-[#D4A72C] text-[#202522] uppercase tracking-wider">
                  <Trophy className="w-3 h-3" />
                  Player Portal
                </span>
                <h2 className="text-xl sm:text-2xl font-serif font-bold text-white mt-1.5">
                  Welcome, {currentUser?.name || 'Competitor'}
                </h2>
                <p className="text-xs text-emerald-100 mt-0.5">
                  Discover upcoming Carrom Championships, submit team registrations, track real-time boards, and view live standings.
                </p>
              </div>

              <div className="bg-emerald-950/70 p-3 rounded-2xl border border-emerald-700/40 text-xs text-emerald-200 shrink-0">
                <div className="font-bold text-white mb-0.5">Registered Competitor ID:</div>
                <div className="text-[11px] text-amber-300 font-mono font-bold">PUNE-CARROM-{(currentUser?.id || 'P1').toUpperCase()}</div>
              </div>
            </div>
          )}

          {/* Tournament Discovery / Selector Hub */}
          <div className="bg-white rounded-3xl border border-gray-200/80 p-6 shadow-xs space-y-4">
            
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
              <div>
                <h3 className="font-serif font-bold text-gray-900 text-lg">
                  Championship Events Explorer
                </h3>
                <p className="text-xs text-gray-500">
                  Select a tournament to view full schedule, boards, live timers, and rankings.
                </p>
              </div>

              {/* Filters */}
              <div className="flex flex-wrap items-center gap-2">
                <div className="relative w-full sm:w-56">
                  <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                  <input
                    type="text"
                    value={searchTerm}
                    onChange={e => setSearchTerm(e.target.value)}
                    placeholder="Search city or event..."
                    className="w-full text-xs pl-9 pr-3 py-2 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                  />
                </div>

                <select
                  value={statusFilter}
                  onChange={e => setStatusFilter(e.target.value as any)}
                  className="text-xs px-3 py-2 border border-gray-200 rounded-xl bg-white focus:ring-2 focus:ring-[#0B5D3B]"
                >
                  <option value="all">All Events</option>
                  <option value="registration_open">Registration Open</option>
                  <option value="ongoing">Live / Ongoing</option>
                  <option value="completed">Completed</option>
                </select>

                <select
                  value={categoryFilter}
                  onChange={e => setCategoryFilter(e.target.value as any)}
                  className="text-xs px-3 py-2 border border-gray-200 rounded-xl bg-white focus:ring-2 focus:ring-[#0B5D3B]"
                >
                  <option value="all">Singles & Doubles</option>
                  <option value="singles">Singles</option>
                  <option value="doubles">Doubles</option>
                </select>
              </div>
            </div>

            {/* Tournament Discovery Cards Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 pt-2">
              {filteredTournaments.map((t) => {
                const isSelected = t.id === activeTournamentId;
                const registrationClosedByDate = isRegistrationDeadlinePassed(t.registrationEndDate, new Date(clockNow));
                const isRegOpen = t.status === 'registration_open' && !registrationClosedByDate;
                const isOngoing = t.status === 'ongoing';
                const isUserRegistered = t.registrations?.some(r => 
                  (currentUser?.id && r.player?.id === currentUser.id) || 
                  (currentUser?.id && r.team?.player1?.id === currentUser.id) ||
                  (currentUser?.id && r.team?.player2?.id === currentUser.id)
                );

                return (
                  <div
                    key={t.id}
                    onClick={() => setActiveTournamentId(t.id)}
                    className={`bg-white rounded-2xl p-5 border transition-all cursor-pointer relative shadow-xs hover:shadow-md flex flex-col justify-between ${
                      isSelected
                        ? 'border-[#0B5D3B] ring-2 ring-[#0B5D3B]/20 bg-emerald-50/20'
                        : 'border-gray-200/80 hover:border-emerald-400'
                    }`}
                  >
                    <div>
                      {/* Status Pills */}
                      <div className="flex items-center justify-between mb-2.5">
                        <span className={`px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider ${
                          isOngoing ? 'bg-orange-100 text-orange-800 animate-pulse' :
                          isRegOpen ? 'bg-emerald-100 text-emerald-800' :
                          'bg-gray-100 text-gray-700'
                        }`}>
                          {registrationClosedByDate && t.status === 'registration_open'
                            ? 'Registration closed'
                            : t.status.replace('_', ' ')}
                        </span>

                        <span className="text-[10px] font-bold text-gray-500 uppercase">
                          {t.category} · {t.format.replace('_', ' ')}
                        </span>
                      </div>

                      <h4 className="font-serif font-bold text-base text-gray-900 leading-snug mb-1">
                        {t.name}
                      </h4>

                      <p className="text-xs text-gray-500 line-clamp-2 mb-3">
                        {t.description}
                      </p>

                      {/* Specs */}
                      <div className="space-y-1.5 text-xs text-gray-600 bg-gray-50 p-3 rounded-xl border border-gray-100 mb-4">
                        <div className="flex items-center justify-between">
                          <span className="text-gray-400 flex items-center gap-1">
                            <MapPin className="w-3.5 h-3.5 text-[#0B5D3B]" />
                            Venue:
                          </span>
                          <span className="font-semibold text-gray-900 truncate max-w-[140px]">{t.venue}</span>
                        </div>

                        <div className="flex items-center justify-between">
                          <span className="text-gray-400 flex items-center gap-1">
                            <Calendar className="w-3.5 h-3.5 text-[#0B5D3B]" />
                            Dates:
                          </span>
                          <span className="font-semibold text-gray-900">{t.tournamentStartDate}</span>
                        </div>

                        <div className="flex items-center justify-between">
                          <span className="text-gray-400 flex items-center gap-1">
                            <Trophy className="w-3.5 h-3.5 text-[#D4A72C]" />
                            Prize Pool:
                          </span>
                          <span className="font-bold text-emerald-800">{t.prizePool}</span>
                        </div>

                        <div className="flex items-center justify-between">
                          <span className="text-gray-400">Entry Fee:</span>
                          <span className="font-bold text-gray-900">₹{t.entryFee}</span>
                        </div>
                      </div>
                    </div>

                    {/* Bottom CTA button */}
                    <div className="flex items-center justify-between pt-2 border-t border-gray-100">
                      {isUserRegistered ? (
                        <span className="px-3 py-1.5 bg-emerald-50 text-emerald-800 text-xs font-bold rounded-xl border border-emerald-200 flex items-center gap-1">
                          <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />
                          <span>Registered</span>
                        </span>
                      ) : isRegOpen ? (
                        <button
                          onClick={(e) => handleOpenRegistration(t, e)}
                          className="px-3.5 py-1.5 bg-[#0B5D3B] hover:bg-[#08472d] text-white text-xs font-bold rounded-xl shadow-xs transition-colors flex items-center gap-1.5"
                        >
                          <UserCheck className="w-3.5 h-3.5 text-[#D4A72C]" />
                          <span>Register Now</span>
                        </button>
                      ) : registrationClosedByDate && t.status === 'registration_open' ? (
                        <span className="text-xs font-semibold text-gray-500">Registration closed</span>
                      ) : (
                        <span className="text-xs font-semibold text-gray-500">
                          {t.matches.length} Scheduled Matches
                        </span>
                      )}

                      <span className="text-xs font-bold text-[#0B5D3B] flex items-center gap-1">
                        <span>{isSelected ? 'Viewing Hub' : 'Open Details'}</span>
                        <ChevronRight className="w-4 h-4" />
                      </span>
                    </div>

                  </div>
                );
              })}
            </div>

          </div>

          {/* Active Tournament Detail Hub for Players */}
          {currentTournament && (
            <div className="bg-white rounded-3xl border border-gray-200/80 shadow-xs overflow-hidden">
              
              {/* Header */}
              <div className="px-6 py-4 bg-[#0B5D3B] text-white flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div>
                  <div className="flex items-center space-x-2">
                    <span className="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded bg-[#D4A72C] text-[#202522]">
                      Tournament Hub
                    </span>
                    <span className="text-xs text-emerald-100">
                      {currentTournament.venue}, {currentTournament.city}
                    </span>
                  </div>
                  <h3 className="font-serif font-bold text-xl text-white mt-1">
                    {currentTournament.name}
                  </h3>
                </div>

                {(userRegistration || (currentTournament.status === 'registration_open' &&
                  !isRegistrationDeadlinePassed(currentTournament.registrationEndDate, new Date(clockNow)))) && (
                  userRegistration ? (
                    // The badge used to read "Registered (Approved)" the instant
                    // someone registered, whatever the row actually said -- and a
                    // self-registration is written as 'pending'. So the approval
                    // step, which the organiser has to perform, was invisible:
                    // the player believed they were in before anyone had decided.
                    (() => {
                      const status = (userRegistration as any).status;

                      // An outstanding fee is the actionable case, so it gets a
                      // button rather than a badge. "Awaiting approval" would be
                      // misleading here: nobody is waiting on the organiser, the
                      // entry is waiting on the money.
                      if (userRegistration.paymentStatus === 'pending' && status !== 'rejected') {
                        return (
                          <div className="flex flex-col items-end gap-1 shrink-0">
                            <span className="text-[10px] text-amber-100 max-w-[17rem] text-right">
                              {paymentInstructions}
                            </span>
                            {checkoutAvailableForEntry && paymentPendingConfirmation !== userRegistration.id &&
                             currentTournament.status !== 'completed' &&
                             (!receivingUpi || gpayProofPending === false) ? (
                            <button
                              onClick={() => settleEntryFee(userRegistration.id)}
                              disabled={payingFee}
                              className="px-4 py-2 bg-[#D4A72C] hover:bg-[#c29623] text-[#202522] text-xs font-bold rounded-xl shadow-md transition-all flex items-center gap-1.5 disabled:opacity-60"
                            >
                              {payingFee
                                ? <Loader2 className="w-4 h-4 animate-spin" />
                                : <CreditCard className="w-4 h-4" />}
                              <span>
                                {payingFee
                                  ? 'Processing…'
                                  : `Razorpay checkout (₹${((userRegistration.feePaise ?? Number(currentTournament.entryFee || 0) * 100) / 100).toLocaleString('en-IN', { maximumFractionDigits: 2 })})`}
                              </span>
                            </button>
                            ) : (
                              <span className="text-[10px] text-amber-300/90 max-w-[16rem] text-right">
                                {gpayProofPending === true
                                  ? 'A payment or receipt is already recorded for this entry. Do not pay again.'
                                  : paymentPendingConfirmation === userRegistration.id
                                    ? 'Payment confirmation is pending. Do not pay again.'
                                    : receivingUpi && gpayProofPending !== false
                                      ? 'Checking earlier payments and receipts before opening another payment.'
                                      : 'Online payment is unavailable. Contact the organiser to settle the entry fee.'}
                              </span>
                            )}
                            <span className="text-[10px] text-amber-300/90">
                              Your entry is confirmed after the fee is verified
                            </span>
                            {feeError && (
                              <span className="text-[10px] text-red-300 max-w-[16rem] text-right">{feeError}</span>
                            )}
                          </div>
                        );
                      }

                      const look =
                        status === 'approved'
                          ? { cls: 'bg-emerald-950/40 text-emerald-300 border-emerald-600/30',
                              icon: 'text-emerald-400', text: "You're in" }
                          : status === 'rejected'
                            ? { cls: 'bg-red-950/40 text-red-300 border-red-600/30',
                                icon: 'text-red-400', text: 'Entry not accepted' }
                            : { cls: 'bg-amber-950/40 text-amber-300 border-amber-600/30',
                                icon: 'text-amber-400', text: 'Awaiting approval' };
                      return (
                        <div className={`px-4 py-2 text-xs font-bold rounded-xl border flex items-center gap-1.5 shrink-0 ${look.cls}`}>
                          <CheckCircle2 className={`w-4 h-4 ${look.icon}`} />
                          <span>{look.text}</span>
                        </div>
                      );
                    })()
                  ) : (
                    <button
                      onClick={() => handleOpenRegistration(currentTournament)}
                      className="px-4 py-2 bg-[#D4A72C] hover:bg-[#c29623] text-[#202522] text-xs font-bold rounded-xl shadow-md transition-all flex items-center gap-1.5 shrink-0"
                    >
                      <UserCheck className="w-4 h-4" />
                      <span>Register My Entry (₹{currentTournament.entryFee})</span>
                    </button>
                  )
                )}
              </div>

              {userRegistration && receivingUpi && paymentPendingConfirmation !== userRegistration.id && (
                <GPayPaymentProof key={userRegistration.id} registration={userRegistration} tournament={currentTournament}
                  requiresBankCheck={razorpayFailedForEntry === userRegistration.id}
                  onPendingChange={pending => setGpayProofPendingByEntry(current => ({
                    ...current, [userRegistration.id]: pending,
                  }))} />
              )}

              <NextMatchCard


                tournament={currentTournament}


                currentUser={currentUser}


                onOpenMatch={setActiveMatch}


              />


              {/* Sub-Tabs */}
              <div className="px-6 pt-3 bg-gray-50 border-b border-gray-200 flex space-x-2 overflow-x-auto">
                {[
                  { id: 'my_matches', label: 'My Matches', icon: Users, badge: myMatches.length },
                  { id: 'schedule', label: 'All Fixtures & Boards', icon: Calendar, badge: currentTournament.matches.length },
                  { id: 'standings', label: 'Points & Standings', icon: Trophy },
                  { id: 'knockout', label: 'Knockout Bracket', icon: Award },
                  { id: 'poster', label: 'Tournament Poster & Rules', icon: Palette }
                ].map((tab) => {
                  const Icon = tab.icon;
                  const isActive = activeTab === tab.id;

                  return (
                    <button
                      key={tab.id}
                      onClick={() => setActiveTab(tab.id as any)}
                      className={`pb-3 px-3 text-xs font-bold border-b-2 flex items-center gap-2 transition-all whitespace-nowrap ${
                        isActive
                          ? 'border-[#0B5D3B] text-[#0B5D3B]'
                          : 'border-transparent text-gray-500 hover:text-gray-800'
                      }`}
                    >
                      <Icon className="w-4 h-4" />
                      <span>{tab.label}</span>
                      {tab.badge !== undefined && (
                        <span className={`px-1.5 py-0.2 rounded-full text-[10px] ${
                          isActive ? 'bg-emerald-100 text-emerald-900' : 'bg-gray-200 text-gray-700'
                        }`}>
                          {tab.badge}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>

              {/* Tab Content */}
              <div className="p-6 bg-[#F8F6F0]/40">
                
                {/* My Matches Tab */}
                {activeTab === 'my_matches' && (
                  <div className="space-y-4">
                    {myMatches.length === 0 ? (
                      <div className="bg-white rounded-2xl p-10 text-center border border-gray-200 shadow-2xs">
                        <Users className="w-10 h-10 text-gray-300 mx-auto mb-2" />
                        <h4 className="text-sm font-bold text-gray-900 mb-1">
                          No Personal Matches Found
                        </h4>
                        <p className="text-xs text-gray-500 max-w-sm mx-auto mb-4">
                          You are viewing as <strong>{currentUser?.name || 'Player'}</strong>. Register your entry or select matches from the full schedule to follow.
                        </p>
                        {currentTournament.status === 'registration_open' &&
                         !isRegistrationDeadlinePassed(currentTournament.registrationEndDate, new Date(clockNow)) && (
                          <button
                            onClick={() => handleOpenRegistration(currentTournament)}
                            className="px-4 py-2 bg-[#0B5D3B] text-white text-xs font-bold rounded-xl shadow-xs hover:bg-[#08472d]"
                          >
                            Register for {currentTournament.name}
                          </button>
                        )}
                      </div>
                    ) : (
                      <>
                      {/* Counts on the tabs: a player can see at a glance that
                          they have one on now and three still to play, without
                          opening each group. */}
                      <div className="flex gap-2">
                        {MINE_GROUPS.map(g => (
                          <button
                            key={g.key}
                            onClick={() => { touchedMineGroup.current = true; setMineGroup(g.key); }}
                            className={`flex-1 py-2 px-2 rounded-xl text-xs font-bold border transition-colors ${
                              mineGroup === g.key
                                ? g.key === 'live'
                                  ? 'bg-red-700 text-white border-red-700'
                                  : 'bg-[#0B5D3B] text-white border-[#0B5D3B]'
                                : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
                            }`}
                          >
                            <span className="inline-flex items-center gap-1.5">
                              {g.key === 'live' && g.rows.length > 0 && (
                                <Radio className={`w-3 h-3 animate-pulse ${
                                  mineGroup === g.key ? 'text-white' : 'text-red-600'}`} />
                              )}
                              {g.label}
                              <span className={mineGroup === g.key ? 'opacity-80' : 'text-gray-400'}>
                                {g.rows.length}
                              </span>
                            </span>
                          </button>
                        ))}
                      </div>

                      {shownMine.rows.length === 0 ? (
                        <div className="bg-white rounded-2xl p-8 text-center border border-gray-200 shadow-2xs text-xs text-gray-500">
                          {shownMine.key === 'live'
                            ? 'None of your matches are being played right now.'
                            : shownMine.key === 'upcoming'
                              ? 'You have played all your matches in this tournament.'
                              : 'No results yet — your finished matches appear here.'}
                        </div>
                      ) : (
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {shownMine.rows.map(m => (
                          <div
                            key={m.id}
                            onClick={() => setActiveMatch(m)}
                            className="bg-white p-4 rounded-2xl border border-gray-200/80 shadow-xs hover:border-emerald-500 transition-all cursor-pointer space-y-3"
                          >
                            <div className="flex items-center justify-between text-xs pb-2 border-b border-gray-100">
                              <span className="font-bold text-[#0B5D3B]">Match #{m.matchNumber} · {m.roundName}</span>
                              <span className="px-2 py-0.5 bg-emerald-100 text-emerald-900 rounded font-bold text-[10px]">
                                Board #{m.boardNumber}
                              </span>
                            </div>

                            <div className="space-y-1.5 text-xs">
                              <div className="flex justify-between font-bold">
                                <span>{m.player1Name}</span>
                                <span>{m.player1TotalPoints} pts ({m.player1BoardWins} wins)</span>
                              </div>
                              <div className="flex justify-between font-bold">
                                <span>{m.player2Name}</span>
                                <span>{m.player2TotalPoints} pts ({m.player2BoardWins} wins)</span>
                              </div>
                            </div>

                            {/* How it ended, and whether this player won it.
                                The points line above says what was scored; it
                                does not say who took the match. */}
                            {(() => {
                              const summary = resultSummary(m);
                              if (!summary) return null;
                              const outcome = outcomeFor(m, currentUser, currentTournament);
                              return (
                                <div className="flex items-center gap-1.5 text-[11px] pt-2 border-t border-gray-100">
                                  {outcome && (
                                    <span className={`shrink-0 text-[9px] font-black uppercase tracking-wide rounded px-1.5 py-0.5 ${
                                      outcome === 'won'
                                        ? 'bg-emerald-100 text-emerald-900'
                                        : 'bg-gray-100 text-gray-600'
                                    }`}>
                                      {outcome}
                                    </span>
                                  )}
                                  <span className="font-semibold text-gray-700 truncate">{summary}</span>
                                  {finishedIsProvisional(m) && (
                                    <span className="ml-auto shrink-0 text-[9px] font-bold uppercase tracking-wide text-amber-700 bg-amber-50 border border-amber-200 rounded px-1.5 py-0.5">
                                      Unconfirmed
                                    </span>
                                  )}
                                </div>
                              );
                            })()}

                            <div className="flex items-center justify-between text-[11px] text-gray-500 pt-2 border-t border-gray-100">
                              <span>{m.scheduledTime || 'Time TBC'}</span>
                              {/* Said accurately per state. Every card used to
                                  offer "Follow Board Live", including matches
                                  that finished on Saturday. */}
                              <span className={`font-bold flex items-center gap-0.5 ${
                                shownMine.key === 'live' ? 'text-red-700' : 'text-[#0B5D3B]'}`}>
                                <span>
                                  {shownMine.key === 'live'
                                    ? 'Follow Board Live'
                                    : shownMine.key === 'finished'
                                      ? 'View Scorecard'
                                      : 'View Match Details'}
                                </span>
                                <ArrowRight className="w-3 h-3" />
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                      )}
                      </>
                    )}
                  </div>
                )}

                {/* Schedule Tab */}
                {activeTab === 'schedule' && (
                  <FixtureScheduleView
                    // Keyed by tournament so it remounts when a different one
                    // is selected. Without this it kept the previous
                    // tournament's search and round filter -- and then saved
                    // them under the NEW tournament's id, so switching between
                    // two tournaments swapped their remembered filters over.
                    key={currentTournament.id}
                    tournament={currentTournament}
                    onOpenMatch={(m) => setActiveMatch(m)}
                  />
                )}

                {/* Standings Tab */}
                {activeTab === 'standings' && (
                  <StandingsSections tournament={currentTournament} />
                )}

                {/* Knockout Tab */}
                {activeTab === 'knockout' && (
                  <KnockoutBracketView
                    tournament={currentTournament}
                    onOpenMatch={(m) => setActiveMatch(m)}
                  />
                )}

                {/* Poster & Rules Tab */}
                {activeTab === 'poster' && (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6 items-center">
                    
                    <TournamentPoster tournament={currentTournament} qr={posterQr} />

                    {/* Rules Overview */}
                    <div className="bg-white p-6 rounded-3xl border border-gray-200 shadow-xs space-y-3 text-xs">
                      <h4 className="font-serif font-bold text-gray-900 text-base">
                        Tournament & Scoring Regulations
                      </h4>
                      <ul className="space-y-2 text-gray-600 list-disc list-inside">
                        <li>{scoringSummary(currentTournament)}. Match duration: {currentTournament.rules.matchDurationMinutes} minutes.</li>
                        {currentTournament.rules.boardEntryMode === 'detailed' && <li>Queen cover and penalties are recorded using the published scoring rules.</li>}
                        <li>Win awards <strong>{currentTournament.rules.pointsForWin} points</strong>, Draw awards <strong>{currentTournament.rules.pointsForDraw} point</strong>, Loss awards <strong>{currentTournament.rules.pointsForLoss} points</strong>.</li>
                        <li>Scheduled rest between rounds: {currentTournament.rules.restTimeMinutes} minutes.</li>
                        <li>Final standings use the tournament's published tiebreaker order. The default is match points, then net score difference, then board difference, then head-to-head.</li>
                      </ul>
                    </div>

                  </div>
                )}

              </div>

            </div>
          )}

        </>
      )}

      {/* Registration Modal */}
      {isRegisterModalOpen && selectedTournamentForReg && (
        <RegistrationFormModal
          tournament={selectedTournamentForReg}
          isOpen={isRegisterModalOpen}
          onClose={() => {
            setIsRegisterModalOpen(false);
            setSelectedTournamentForReg(null);
          }}
        />
      )}

    </div>
  );
};
