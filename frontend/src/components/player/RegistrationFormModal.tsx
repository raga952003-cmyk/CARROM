import React, { useState } from 'react';
import { 
  X, 
  Check, 
  Users, 
  Trophy, 
  ShieldCheck, 
  Sparkles, 
  Calendar, 
  CreditCard,
  UserCheck,
  AlertTriangle
} from 'lucide-react';
import confetti from 'canvas-confetti';
import { Tournament, Player, Team } from '../../types/tournament';
import { useTournament } from '../../context/TournamentContext';

interface RegistrationFormModalProps {
  tournament: Tournament;
  isOpen: boolean;
  onClose: () => void;
}

export const RegistrationFormModal: React.FC<RegistrationFormModalProps> = ({
  tournament,
  isOpen,
  onClose
}) => {
  const { registerForTournament, currentUser } = useTournament();

  const [regType, setRegType] = useState<'singles' | 'doubles'>(
    tournament.category === 'doubles' ? 'doubles' : 'singles'
  );

  // Singles Fields
  const [playerName, setPlayerName] = useState(currentUser?.name || '');
  const [phone, setPhone] = useState(currentUser?.phone || '');
  const [email, setEmail] = useState(currentUser?.email || '');
  const [club, setClub] = useState(currentUser?.club || '');
  const [city, setCity] = useState(currentUser?.city || tournament.city || 'Pune');

  // Doubles Team Fields. These start empty on purpose: pre-filled sample values
  // were being submitted verbatim, registering a fictitious partner.
  const [teamName, setTeamName] = useState('');
  const [partnerName, setPartnerName] = useState('');
  const [partnerPhone, setPartnerPhone] = useState('');
  const [partnerEmail, setPartnerEmail] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const [isSuccess, setIsSuccess] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');

  if (!isOpen) return null;

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
        rating: currentUser?.rating || 1500
      };

      if (regType === 'singles') {
        await registerForTournament(tournament.id, 'singles', self);
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
        await registerForTournament(tournament.id, 'doubles', newTeam);
      }

      setIsSuccess(true);
      try {
        confetti({
          particleCount: 80,
          spread: 60,
          origin: { y: 0.6 }
        });
      } catch (err) {}
    } catch (error: any) {
      setErrorMsg(
        error?.message ||
          'Failed to register. You might already be registered in this tournament.'
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-black/60 backdrop-blur-xs flex items-center justify-center p-4 animate-in fade-in duration-150">
      <div className="relative bg-white rounded-3xl max-w-lg w-full p-6 shadow-2xl border border-gray-100 overflow-hidden">
        
        {/* Close Button */}
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-gray-400 hover:text-gray-600 p-1.5 rounded-lg hover:bg-gray-100 transition-colors"
        >
          <X className="w-5 h-5" />
        </button>

        {isSuccess ? (
          /* Success Screen */
          <div className="text-center py-6 space-y-4">
            <div className="w-16 h-16 rounded-full bg-emerald-100 text-[#0B5D3B] flex items-center justify-center mx-auto shadow-inner">
              <UserCheck className="w-8 h-8 text-emerald-600" />
            </div>

            <div>
              <span className="text-[10px] font-black text-[#D4A72C] uppercase tracking-widest bg-emerald-950 px-3 py-1 rounded-full">
                Registration Confirmed
              </span>
              <h3 className="font-serif font-bold text-2xl text-gray-900 mt-2">
                You're in the Tournament!
              </h3>
              <p className="text-xs text-gray-500 mt-1 max-w-sm mx-auto">
                Your entry for <strong>{tournament.name}</strong> has been registered. You'll receive real-time schedule alerts once boards are assigned.
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
                <strong className="text-emerald-700">₹{tournament.entryFee} (Verified)</strong>
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
                Entry Fee: <strong>₹{tournament.entryFee}</strong> · Deadline: <strong>{tournament.registrationEndDate}</strong>
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
                    placeholder="e.g. Pune Striker Kings"
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
                    placeholder="e.g. Pune"
                  />
                </div>
              </div>

            </div>

            {/* Payment Guarantee Notice */}
            <div className="p-3 bg-emerald-50 rounded-xl border border-emerald-200 flex items-center justify-between text-xs">
              <div className="flex items-center space-x-2 text-emerald-900">
                <CreditCard className="w-4 h-4 text-emerald-700 shrink-0" />
                <span>Entry Fee: <strong>₹{tournament.entryFee}</strong></span>
              </div>
              <span className="text-[10px] font-bold text-emerald-800 bg-white px-2 py-0.5 rounded border border-emerald-200">
                On-Site / UPI Verified
              </span>
            </div>

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
                <Check className="w-4 h-4 text-[#D4A72C]" />
                <span>{isSubmitting ? 'Submitting...' : 'Confirm & Submit Entry'}</span>
              </button>
            </div>

          </form>
        )}

      </div>
    </div>
  );
};
