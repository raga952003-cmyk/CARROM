

import React, { useState, useEffect } from 'react';
import { 
  Users, 
  Search, 
  Filter, 
  Check, 
  X, 
  Lock, 
  UserPlus, 
  ShieldCheck, 
  Clock, 
  Calendar,
  AlertCircle,
  Trophy,
  CheckCircle2,
  Upload
} from 'lucide-react';
import { Tournament, Registration, Player, Team } from '../../types/tournament';
import { useTournament } from '../../context/TournamentContext';
import { ConfirmationModal } from '../common/ConfirmationModal';
import { ImportParticipantsModal } from './ImportParticipantsModal';

interface RegistrationManagerProps {
  tournament: Tournament;
}

export const RegistrationManager: React.FC<RegistrationManagerProps> = ({ tournament }) => {
  const { 
    approveRegistration, 
    rejectRegistration, 
    closeRegistration, 
    allPlayers, 
    allTeams, 
    registerForTournament,
    createPlayerAccount
  } = useTournament();

  const [searchTerm, setSearchTerm] = useState('');
  const [filterType, setFilterType] = useState<'all' | 'singles' | 'doubles'>('all');
  const [filterStatus, setFilterStatus] = useState<'all' | 'approved' | 'pending' | 'rejected'>('all');
  const [isCloseModalOpen, setIsCloseModalOpen] = useState(false);
  const [isAddPlayerModalOpen, setIsAddPlayerModalOpen] = useState(false);
  const [isImportModalOpen, setIsImportModalOpen] = useState(false);
  
  const [selectedPlayerId, setSelectedPlayerId] = useState<string>(allPlayers[0]?.id || '');
  const [selectedTeamId, setSelectedTeamId] = useState<string>(allTeams[0]?.id || '');
  const [regType, setRegType] = useState<'singles' | 'doubles'>('singles');

  const [addMode, setAddMode] = useState<'select' | 'create'>('select');
  const [newName, setNewName] = useState('');
  const [newClub, setNewClub] = useState('');
  const [newCity, setNewCity] = useState('');
  const [newRating, setNewRating] = useState('1500');
  const [newSeed, setNewSeed] = useState('');

  // Doubles entry paths. 'players' pairs two people already on the roster,
  // 'create' registers a partner who has no account yet, and 'team' reuses a
  // pair that has played together before.
  const [doublesMode, setDoublesMode] = useState<'players' | 'create' | 'team'>('players');
  const [teamName, setTeamName] = useState('');
  const [partnerAId, setPartnerAId] = useState<string>('');
  const [partnerBId, setPartnerBId] = useState<string>('');
  const [newPartnerName, setNewPartnerName] = useState('');
  const [newPartnerPhone, setNewPartnerPhone] = useState('');
  const [newPartnerEmail, setNewPartnerEmail] = useState('');
  const [newPartnerClub, setNewPartnerClub] = useState('');

  const [addError, setAddError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  // The roster arrives asynchronously, so seed the pickers when it lands
  // instead of leaving them pinned to the empty initial value.
  useEffect(() => {
    if (allPlayers.length === 0) return;
    setSelectedPlayerId(prev => prev || allPlayers[0].id);
    setPartnerAId(prev => prev || allPlayers[0].id);
    setPartnerBId(prev => prev || allPlayers[1]?.id || '');
  }, [allPlayers]);

  useEffect(() => {
    if (allTeams.length > 0) {
      setSelectedTeamId(prev => prev || allTeams[0].id);
    }
  }, [allTeams]);

  const registrations = tournament.registrations || [];
  
  const singlesCount = registrations.filter(r => r.type === 'singles').length;
  const doublesCount = registrations.filter(r => r.type === 'doubles').length;
  const approvedCount = registrations.filter(r => r.status === 'approved').length;
  const pendingCount = registrations.filter(r => r.status === 'pending').length;

  const filteredRegistrations = registrations.filter(reg => {
    const name = reg.type === 'singles' 
      ? reg.player?.name || '' 
      : reg.team?.name || `${reg.team?.player1?.name || ''} & ${reg.team?.player2?.name || ''}`;
    const club = reg.player?.club || reg.team?.club || '';
    
    const matchesSearch = name.toLowerCase().includes(searchTerm.toLowerCase()) || 
                          club.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesType = filterType === 'all' || reg.type === filterType;
    const matchesStatus = filterStatus === 'all' || reg.status === filterStatus;

    return matchesSearch && matchesType && matchesStatus;
  });

  const resetAddForm = () => {
    setNewName('');
    setNewClub('');
    setNewCity('');
    setNewRating('1500');
    setNewSeed('');
    setTeamName('');
    setNewPartnerName('');
    setNewPartnerPhone('');
    setNewPartnerEmail('');
    setNewPartnerClub('');
    setAddMode('select');
    setAddError('');
  };

  const handleManualAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    setAddError('');
    setIsSubmitting(true);

    try {
      if (regType === 'singles') {
        if (addMode === 'create') {
          if (!newName.trim()) {
            setAddError('Enter the player name.');
            return;
          }
          const newPlayerId = await createPlayerAccount({
            name: newName.trim(),
            club: newClub || 'Independent',
            city: newCity || undefined,
            rating: Number(newRating) || 1500,
            seed: newSeed ? Number(newSeed) : undefined
          });
          await registerForTournament(tournament.id, 'singles', {
            id: newPlayerId,
            name: newName.trim(),
            club: newClub || 'Independent',
            city: newCity || undefined,
            rating: Number(newRating) || 1500,
            seed: newSeed ? Number(newSeed) : undefined
          } as Player);
        } else {
          const p = allPlayers.find(pl => pl.id === selectedPlayerId);
          if (!p) {
            setAddError('Select a player to register.');
            return;
          }
          await registerForTournament(tournament.id, 'singles', p);
        }
      } else if (doublesMode === 'team') {
        const t = allTeams.find(tm => tm.id === selectedTeamId);
        if (!t) {
          setAddError('Select a team to register.');
          return;
        }
        await registerForTournament(tournament.id, 'doubles', t);
      } else if (doublesMode === 'players') {
        const p1 = allPlayers.find(pl => pl.id === partnerAId);
        const p2 = allPlayers.find(pl => pl.id === partnerBId);
        if (!p1 || !p2) {
          setAddError('Choose both players for the team.');
          return;
        }
        if (p1.id === p2.id) {
          setAddError('A team needs two different players.');
          return;
        }
        await registerForTournament(tournament.id, 'doubles', {
          id: '',
          name: teamName.trim() || (p1.name + ' & ' + p2.name),
          player1: p1,
          player2: p2
        } as Team);
      } else {
        // Partner has no account yet; the backend creates their profile.
        const p1 = allPlayers.find(pl => pl.id === partnerAId);
        if (!p1) {
          setAddError('Choose the first player from the roster.');
          return;
        }
        if (!newPartnerName.trim()) {
          setAddError('Enter the partner name.');
          return;
        }
        await registerForTournament(tournament.id, 'doubles', {
          id: '',
          name: teamName.trim() || (p1.name + ' & ' + newPartnerName.trim()),
          player1: p1,
          player2: {
            id: '',
            name: newPartnerName.trim(),
            phone: newPartnerPhone || undefined,
            email: newPartnerEmail || undefined,
            club: newPartnerClub || p1.club
          }
        } as Team);
      }

      resetAddForm();
      setIsAddPlayerModalOpen(false);
    } catch (error: any) {
      setAddError(error?.message || 'Could not register this entry.');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div id="registration-manager" className="space-y-5">
      
      {/* Registration Stats & Status Header */}
      <div className="bg-white p-5 rounded-2xl border border-gray-200/80 shadow-xs flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center space-x-2">
            <span className={`px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider ${
              tournament.status === 'registration_open'
                ? 'bg-emerald-100 text-emerald-800'
                : 'bg-gray-100 text-gray-700'
            }`}>
              {tournament.status.replace('_', ' ')}
            </span>
            <span className="text-xs text-gray-500 flex items-center gap-1">
              <Calendar className="w-3.5 h-3.5" />
              Deadline: {tournament.registrationEndDate}
            </span>
          </div>

          <h3 className="text-lg font-serif font-bold text-gray-900 mt-1">
            Participant Registration Management
          </h3>
          <p className="text-xs text-gray-600">
            Review player applications, verify payment/eligibility, and approve seeded entries.
          </p>
        </div>

        {/* Action CTAs */}
        <div className="flex items-center space-x-2 shrink-0">
          <button
            type="button"
            onClick={() => setIsAddPlayerModalOpen(true)}
            className="px-3.5 py-2 bg-emerald-50 hover:bg-emerald-100 text-[#0B5D3B] text-xs font-bold rounded-xl border border-emerald-200 transition-colors flex items-center gap-1.5"
          >
            <UserPlus className="w-4 h-4" />
            <span>Add Participant</span>
          </button>

          <button
            type="button"
            onClick={() => setIsImportModalOpen(true)}
            className="px-3.5 py-2 bg-amber-50 hover:bg-[#D4A72C]/10 text-amber-800 text-xs font-bold rounded-xl border border-amber-200 transition-colors flex items-center gap-1.5"
            title="Import Excel or CSV list"
          >
            <Upload className="w-4 h-4 text-[#D4A72C]" />
            <span>Import Participants</span>
          </button>

          {tournament.status === 'registration_open' && (
            <button
              onClick={() => setIsCloseModalOpen(true)}
              className="px-3.5 py-2 bg-amber-600 hover:bg-amber-700 text-white text-xs font-bold rounded-xl shadow-xs transition-colors flex items-center gap-1.5"
            >
              <Lock className="w-4 h-4" />
              <span>Close Registration</span>
            </button>
          )}
        </div>
      </div>

      {/* Summary Metric Badges */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="bg-white p-3 rounded-xl border border-gray-200 shadow-2xs">
          <div className="text-[10px] font-semibold text-gray-500 uppercase">Total Entries</div>
          <div className="text-xl font-bold text-gray-900 mt-0.5">{registrations.length}</div>
        </div>

        <div className="bg-white p-3 rounded-xl border border-gray-200 shadow-2xs">
          <div className="text-[10px] font-semibold text-gray-500 uppercase">Singles Players</div>
          <div className="text-xl font-bold text-emerald-700 mt-0.5">{singlesCount}</div>
        </div>

        <div className="bg-white p-3 rounded-xl border border-gray-200 shadow-2xs">
          <div className="text-[10px] font-semibold text-gray-500 uppercase">Doubles Teams</div>
          <div className="text-xl font-bold text-blue-700 mt-0.5">{doublesCount}</div>
        </div>

        <div className="bg-white p-3 rounded-xl border border-gray-200 shadow-2xs">
          <div className="text-[10px] font-semibold text-gray-500 uppercase">Approved Status</div>
          <div className="text-xl font-bold text-emerald-800 mt-0.5">
            {approvedCount} <span className="text-xs text-gray-500 font-normal">/ {registrations.length}</span>
          </div>
        </div>
      </div>

      {/* Search and Filters */}
      <div className="bg-white p-4 rounded-2xl border border-gray-200/80 shadow-xs space-y-3">
        <div className="flex flex-col sm:flex-row gap-3 items-center justify-between">
          
          <div className="relative w-full sm:w-72">
            <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={searchTerm}
              onChange={e => setSearchTerm(e.target.value)}
              placeholder="Search by player or club..."
              className="w-full text-xs pl-9 pr-3 py-2 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
            />
          </div>

          <div className="flex items-center space-x-2 w-full sm:w-auto">
            {/* Category Filter */}
            <select
              value={filterType}
              onChange={e => setFilterType(e.target.value as any)}
              className="text-xs px-3 py-2 border border-gray-200 rounded-xl bg-white focus:ring-2 focus:ring-[#0B5D3B]"
            >
              <option value="all">All Categories</option>
              <option value="singles">Singles Only</option>
              <option value="doubles">Doubles Only</option>
            </select>

            {/* Status Filter */}
            <select
              value={filterStatus}
              onChange={e => setFilterStatus(e.target.value as any)}
              className="text-xs px-3 py-2 border border-gray-200 rounded-xl bg-white focus:ring-2 focus:ring-[#0B5D3B]"
            >
              <option value="all">All Statuses</option>
              <option value="approved">Approved</option>
              <option value="pending">Pending</option>
              <option value="rejected">Rejected</option>
            </select>
          </div>

        </div>

        {/* Registrations Table */}
        <div className="overflow-x-auto rounded-xl border border-gray-100">
          <table className="w-full text-left text-xs">
            <thead className="bg-gray-50 text-gray-600 font-semibold border-b border-gray-200">
              <tr>
                <th className="px-4 py-3">Participant / Team</th>
                <th className="px-3 py-3">Category</th>
                <th className="px-3 py-3">Club / City</th>
                <th className="px-3 py-3">Seed / Rating</th>
                <th className="px-3 py-3">Payment</th>
                <th className="px-3 py-3">Status</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {filteredRegistrations.length === 0 ? (
                <tr>
                  <td colSpan={7} className="text-center py-10 text-gray-500">
                    <Users className="w-8 h-8 mx-auto text-gray-300 mb-2" />
                    No registrations found matching your criteria.
                  </td>
                </tr>
              ) : (
                filteredRegistrations.map((reg) => {
                  const isSingles = reg.type === 'singles';
                  const title = isSingles ? reg.player?.name : reg.team?.name;
                  const club = isSingles ? reg.player?.club : reg.team?.club;
                  const city = isSingles ? reg.player?.city : reg.team?.city;
                  const rating = isSingles ? reg.player?.rating : reg.team?.rating;
                  const seed = isSingles ? reg.player?.seed : reg.team?.seed;

                  return (
                    <tr key={reg.id} className="hover:bg-gray-50/80 transition-colors">
                      <td className="px-4 py-3">
                        <div className="font-bold text-gray-900">{title}</div>
                        {!isSingles && reg.team && (
                          <div className="text-[10px] text-gray-500">
                            {reg.team.player1.name} & {reg.team.player2.name}
                          </div>
                        )}
                      </td>

                      <td className="px-3 py-3">
                        <span className={`inline-flex px-2 py-0.5 rounded text-[10px] font-semibold uppercase ${
                          isSingles ? 'bg-emerald-100 text-emerald-800' : 'bg-blue-100 text-blue-800'
                        }`}>
                          {reg.type}
                        </span>
                      </td>

                      <td className="px-3 py-3 text-gray-600">
                        <div>{club || 'Independent'}</div>
                        <div className="text-[10px] text-gray-400">{city}</div>
                      </td>

                      <td className="px-3 py-3">
                        <div className="font-semibold text-gray-800">{rating ? `${rating} pts` : 'Unrated'}</div>
                        {seed && (
                          <span className="text-[10px] text-[#D4A72C] font-bold">Seed #{seed}</span>
                        )}
                      </td>

                      <td className="px-3 py-3">
                        <span className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-700">
                          <CheckCircle2 className="w-3 h-3 text-emerald-600" />
                          ₹{tournament.entryFee} (Paid)
                        </span>
                      </td>

                      <td className="px-3 py-3">
                        <span className={`inline-flex px-2 py-0.5 rounded-full text-[10px] font-bold capitalize ${
                          reg.status === 'approved'
                            ? 'bg-emerald-100 text-emerald-800'
                            : reg.status === 'rejected'
                            ? 'bg-red-100 text-red-800'
                            : 'bg-amber-100 text-amber-800'
                        }`}>
                          {reg.status}
                        </span>
                      </td>

                      <td className="px-4 py-3 text-right">
                        <div className="flex items-center justify-end space-x-1">
                          {reg.status !== 'approved' && (
                            <button
                              onClick={() => approveRegistration(tournament.id, reg.id)}
                              className="p-1 text-emerald-700 hover:bg-emerald-100 rounded transition-colors"
                              title="Approve entry"
                            >
                              <Check className="w-4 h-4" />
                            </button>
                          )}

                          {reg.status !== 'rejected' && (
                            <button
                              onClick={() => rejectRegistration(tournament.id, reg.id)}
                              className="p-1 text-red-600 hover:bg-red-100 rounded transition-colors"
                              title="Reject entry"
                            >
                              <X className="w-4 h-4" />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Confirmation Modal for Closing Registration */}
      <ConfirmationModal
        isOpen={isCloseModalOpen}
        onClose={() => setIsCloseModalOpen(false)}
        onConfirm={() => closeRegistration(tournament.id)}
        title="Close Registration Window?"
        description="Closing registrations will finalize the player pool and unlock automatic fixture and schedule generation. New player registrations will be disabled."
        confirmLabel="Close Registration"
        variant="warning"
      />

      {/* Add Player / Team Modal */}
      {isAddPlayerModalOpen && (
        <div className="fixed inset-0 z-50 overflow-y-auto bg-black/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-100">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-bold text-gray-900 text-base">Add Participant / Seed Entry</h3>
              <button onClick={() => setIsAddPlayerModalOpen(false)} className="text-gray-400 hover:text-gray-600">
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleManualAdd} className="space-y-4 text-xs">
              <div>
                <label className="block font-bold text-gray-700 mb-1">Registration Category</label>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => setRegType('singles')}
                    className={`py-2 text-center rounded-lg border font-semibold ${
                      regType === 'singles' ? 'bg-emerald-50 border-[#0B5D3B] text-[#0B5D3B]' : 'border-gray-200'
                    }`}
                  >
                    Singles Player
                  </button>
                  <button
                    type="button"
                    onClick={() => setRegType('doubles')}
                    className={`py-2 text-center rounded-lg border font-semibold ${
                      regType === 'doubles' ? 'bg-blue-50 border-blue-600 text-blue-700' : 'border-gray-200'
                    }`}
                  >
                    Doubles Team
                  </button>
                </div>
              </div>

              {regType === 'singles' && (
                <div>
                  <label className="block font-bold text-gray-700 mb-1">Player Option</label>
                  <div className="grid grid-cols-2 gap-2">
                    <button
                      type="button"
                      onClick={() => setAddMode('select')}
                      className={`py-1.5 text-center rounded-lg border font-semibold ${
                        addMode === 'select' ? 'bg-emerald-50 border-[#0B5D3B] text-[#0B5D3B]' : 'border-gray-200 text-gray-600'
                      }`}
                    >
                      Select Existing
                    </button>
                    <button
                      type="button"
                      onClick={() => setAddMode('create')}
                      className={`py-1.5 text-center rounded-lg border font-semibold ${
                        addMode === 'create' ? 'bg-emerald-50 border-[#0B5D3B] text-[#0B5D3B]' : 'border-gray-200 text-gray-600'
                      }`}
                    >
                      Create New Player
                    </button>
                  </div>
                </div>
              )}

              {regType === 'singles' ? (
                addMode === 'select' ? (
                  <div>
                    <label className="block font-bold text-gray-700 mb-1">Select Player</label>
                    <select
                      value={selectedPlayerId}
                      onChange={e => setSelectedPlayerId(e.target.value)}
                      className="w-full p-2.5 border border-gray-200 rounded-lg bg-white"
                    >
                      {allPlayers.map(p => (
                        <option key={p.id} value={p.id}>
                          {p.name} ({p.club} - Rating: {p.rating})
                        </option>
                      ))}
                    </select>
                  </div>
                ) : (
                  <div className="space-y-3">
                    <div>
                      <label className="block font-bold text-gray-700 mb-1">Full Name *</label>
                      <input
                        type="text"
                        required
                        value={newName}
                        onChange={e => setNewName(e.target.value)}
                        placeholder="e.g. John Doe"
                        className="w-full p-2 border border-gray-200 rounded-lg bg-white text-xs"
                      />
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="block font-bold text-gray-700 mb-1">Club</label>
                        <input
                          type="text"
                          value={newClub}
                          onChange={e => setNewClub(e.target.value)}
                          placeholder="e.g. Independent"
                          className="w-full p-2 border border-gray-200 rounded-lg bg-white text-xs"
                        />
                      </div>
                      <div>
                        <label className="block font-bold text-gray-700 mb-1">City</label>
                        <input
                          type="text"
                          value={newCity}
                          onChange={e => setNewCity(e.target.value)}
                          placeholder="City"
                          className="w-full p-2 border border-gray-200 rounded-lg bg-white text-xs"
                        />
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="block font-bold text-gray-700 mb-1">Rating</label>
                        <input
                          type="number"
                          value={newRating}
                          onChange={e => setNewRating(e.target.value)}
                          placeholder="1500"
                          className="w-full p-2 border border-gray-200 rounded-lg bg-white text-xs"
                        />
                      </div>
                      <div>
                        <label className="block font-bold text-gray-700 mb-1">Seed (Optional)</label>
                        <input
                          type="number"
                          value={newSeed}
                          onChange={e => setNewSeed(e.target.value)}
                          placeholder="None"
                          className="w-full p-2 border border-gray-200 rounded-lg bg-white text-xs"
                        />
                      </div>
                    </div>
                  </div>
                )
              ) : (
                <div className="space-y-3">
                  <div>
                    <label className="block font-bold text-gray-700 mb-1">Team Option</label>
                    <div className="grid grid-cols-3 gap-2">
                      <button
                        type="button"
                        onClick={() => setDoublesMode('players')}
                        className={`py-1.5 text-center rounded-lg border font-semibold ${
                          doublesMode === 'players' ? 'bg-blue-50 border-blue-600 text-blue-700' : 'border-gray-200 text-gray-600'
                        }`}
                      >
                        Pair Two Players
                      </button>
                      <button
                        type="button"
                        onClick={() => setDoublesMode('create')}
                        className={`py-1.5 text-center rounded-lg border font-semibold ${
                          doublesMode === 'create' ? 'bg-blue-50 border-blue-600 text-blue-700' : 'border-gray-200 text-gray-600'
                        }`}
                      >
                        New Partner
                      </button>
                      <button
                        type="button"
                        onClick={() => setDoublesMode('team')}
                        disabled={allTeams.length === 0}
                        title={allTeams.length === 0 ? 'No saved teams yet' : 'Reuse an existing pair'}
                        className={`py-1.5 text-center rounded-lg border font-semibold ${
                          doublesMode === 'team' ? 'bg-blue-50 border-blue-600 text-blue-700' : 'border-gray-200 text-gray-600'
                        } ${allTeams.length === 0 ? 'opacity-40 cursor-not-allowed' : ''}`}
                      >
                        Existing Team
                      </button>
                    </div>
                  </div>

                  <div>
                    <label className="block font-bold text-gray-700 mb-1">Team Name (Optional)</label>
                    <input
                      type="text"
                      value={teamName}
                      onChange={e => setTeamName(e.target.value)}
                      placeholder="Auto-generated from both player names"
                      className="w-full p-2.5 border border-gray-200 rounded-lg bg-white text-xs"
                    />
                  </div>

                  {doublesMode === 'team' ? (
                    <div>
                      <label className="block font-bold text-gray-700 mb-1">Select Doubles Team</label>
                      <select
                        value={selectedTeamId}
                        onChange={e => setSelectedTeamId(e.target.value)}
                        className="w-full p-2.5 border border-gray-200 rounded-lg bg-white"
                      >
                        {allTeams.map(t => (
                          <option key={t.id} value={t.id}>
                            {t.name} ({t.player1?.name} & {t.player2?.name})
                          </option>
                        ))}
                      </select>
                    </div>
                  ) : (
                    <>
                      <div>
                        <label className="block font-bold text-gray-700 mb-1">Player 1 *</label>
                        {allPlayers.length === 0 ? (
                          <p className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-lg p-2">
                            No players on the roster yet. Add one via Manage Players, or register a
                            singles player first.
                          </p>
                        ) : (
                          <select
                            value={partnerAId}
                            onChange={e => setPartnerAId(e.target.value)}
                            className="w-full p-2.5 border border-gray-200 rounded-lg bg-white"
                          >
                            {allPlayers.map(p => (
                              <option key={p.id} value={p.id}>
                                {p.name} ({p.club} - Rating: {p.rating})
                              </option>
                            ))}
                          </select>
                        )}
                      </div>

                      {doublesMode === 'players' ? (
                        <div>
                          <label className="block font-bold text-gray-700 mb-1">Player 2 *</label>
                          <select
                            value={partnerBId}
                            onChange={e => setPartnerBId(e.target.value)}
                            className="w-full p-2.5 border border-gray-200 rounded-lg bg-white"
                          >
                            <option value="">Select partner...</option>
                            {allPlayers
                              .filter(p => p.id !== partnerAId)
                              .map(p => (
                                <option key={p.id} value={p.id}>
                                  {p.name} ({p.club} - Rating: {p.rating})
                                </option>
                              ))}
                          </select>
                        </div>
                      ) : (
                        <div className="space-y-2 border border-blue-100 bg-blue-50/40 rounded-lg p-3">
                          <p className="text-[11px] text-blue-800 font-semibold">
                            Partner details — a player account is created automatically.
                          </p>
                          <div>
                            <label className="block font-bold text-gray-700 mb-1">Partner Name *</label>
                            <input
                              type="text"
                              value={newPartnerName}
                              onChange={e => setNewPartnerName(e.target.value)}
                              placeholder="e.g. Sunil Jadhav"
                              className="w-full p-2 border border-gray-200 rounded-lg bg-white text-xs"
                            />
                          </div>
                          <div className="grid grid-cols-2 gap-2">
                            <div>
                              <label className="block font-bold text-gray-700 mb-1">Phone</label>
                              <input
                                type="tel"
                                value={newPartnerPhone}
                                onChange={e => setNewPartnerPhone(e.target.value)}
                                placeholder="Optional"
                                className="w-full p-2 border border-gray-200 rounded-lg bg-white text-xs"
                              />
                            </div>
                            <div>
                              <label className="block font-bold text-gray-700 mb-1">Club</label>
                              <input
                                type="text"
                                value={newPartnerClub}
                                onChange={e => setNewPartnerClub(e.target.value)}
                                placeholder="Independent"
                                className="w-full p-2 border border-gray-200 rounded-lg bg-white text-xs"
                              />
                            </div>
                          </div>
                          <div>
                            <label className="block font-bold text-gray-700 mb-1">Email</label>
                            <input
                              type="email"
                              value={newPartnerEmail}
                              onChange={e => setNewPartnerEmail(e.target.value)}
                              placeholder="Optional - reuses an existing player if it matches"
                              className="w-full p-2 border border-gray-200 rounded-lg bg-white text-xs"
                            />
                          </div>
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}

              {addError && (
                <div className="text-[11px] text-red-700 bg-red-50 border border-red-200 rounded-lg p-2 font-medium">
                  {addError}
                </div>
              )}

              <div className="pt-2 flex justify-end space-x-2">
                <button
                  type="button"
                  onClick={() => setIsAddPlayerModalOpen(false)}
                  className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="px-4 py-2 bg-[#0B5D3B] text-white font-bold rounded-lg shadow-sm hover:bg-[#08472d] disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {isSubmitting ? 'Registering...' : 'Register Participant'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Import Participants Modal */}
      {isImportModalOpen && (
        <ImportParticipantsModal
          isOpen={isImportModalOpen}
          onClose={() => setIsImportModalOpen(false)}
          tournament={tournament}
        />
      )}

    </div>
  );
};
