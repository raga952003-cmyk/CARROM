import React, { useState } from 'react';
import { 
  Users, 
  Search, 
  UserPlus, 
  Edit3, 
  Trash2, 
  X, 
  Check, 
  ShieldAlert, 
  Award,
  Building,
  MapPin,
  Mail,
  Phone,
  Flame
} from 'lucide-react';
import { Player } from '../../types/tournament';
import { useTournament } from '../../context/TournamentContext';

export const ManagePlayersTab: React.FC = () => {
  const { allPlayers, createPlayerAccount, updatePlayerAccount, deletePlayerAccount } = useTournament();

  const [searchTerm, setSearchTerm] = useState('');
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingPlayer, setEditingPlayer] = useState<Player | null>(null);

  // Form Fields
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [club, setClub] = useState('');
  const [city, setCity] = useState('Pune');
  const [rating, setRating] = useState(1500);
  const [seed, setSeed] = useState<number | undefined>(undefined);

  const filteredPlayers = allPlayers.filter(p => 
    p.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    (p.club && p.club.toLowerCase().includes(searchTerm.toLowerCase())) ||
    (p.city && p.city.toLowerCase().includes(searchTerm.toLowerCase())) ||
    (p.email && p.email.toLowerCase().includes(searchTerm.toLowerCase()))
  );

  const handleOpenAdd = () => {
    setEditingPlayer(null);
    setName('');
    setEmail('');
    setPhone('');
    setClub('');
    setCity('Pune');
    setRating(1500);
    setSeed(undefined);
    setIsModalOpen(true);
  };

  const handleOpenEdit = (p: Player) => {
    setEditingPlayer(p);
    setName(p.name);
    setEmail(p.email || '');
    setPhone(p.phone || '');
    setClub(p.club || '');
    setCity(p.city || 'Pune');
    setRating(p.rating || 1500);
    setSeed(p.seed);
    setIsModalOpen(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const playerData = {
      name,
      email: email || undefined,
      phone: phone || undefined,
      club: club || undefined,
      city,
      rating,
      seed: seed ? Number(seed) : undefined
    };

    if (editingPlayer) {
      await updatePlayerAccount(editingPlayer.id, playerData);
    } else {
      await createPlayerAccount(playerData);
    }
    setIsModalOpen(false);
  };

  const handleDelete = async (id: string, playerName: string) => {
    if (window.confirm(`Are you sure you want to permanently delete player account "${playerName}"?`)) {
      await deletePlayerAccount(id);
    }
  };

  return (
    <div className="space-y-5">
      
      {/* Tab Header Card */}
      <div className="bg-white p-5 rounded-2xl border border-gray-200/80 shadow-xs flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h3 className="text-lg font-serif font-bold text-gray-900 flex items-center gap-2">
            <Users className="w-5 h-5 text-[#0B5D3B]" />
            <span>Players Directory & Profiles</span>
          </h3>
          <p className="text-xs text-gray-600">
            Create, update, and maintain player accounts, seeds, clubs, ratings, and contact info in the database.
          </p>
        </div>

        <button
          onClick={handleOpenAdd}
          className="px-4 py-2.5 bg-[#0B5D3B] hover:bg-[#094e32] text-white text-xs font-bold rounded-xl shadow-md transition-all flex items-center justify-center gap-1.5 self-start md:self-auto shrink-0"
        >
          <UserPlus className="w-4 h-4 text-[#D4A72C]" />
          <span>Create Player Profile</span>
        </button>
      </div>

      {/* Stats Quick Grid */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <div className="bg-white p-4 rounded-xl border border-gray-200 shadow-2xs">
          <div className="text-[10px] font-semibold text-gray-500 uppercase">Total Competitors</div>
          <div className="text-xl font-bold text-gray-900 mt-0.5">{allPlayers.length}</div>
        </div>

        <div className="bg-white p-4 rounded-xl border border-gray-200 shadow-2xs">
          <div className="text-[10px] font-semibold text-gray-500 uppercase">Avg Player Rating</div>
          <div className="text-xl font-bold text-emerald-800 mt-0.5">
            {allPlayers.length > 0 
              ? Math.round(allPlayers.reduce((acc, p) => acc + (p.rating || 1500), 0) / allPlayers.length)
              : 0} pts
          </div>
        </div>

        <div className="bg-white p-4 rounded-xl border border-gray-200 shadow-2xs col-span-2 sm:col-span-1">
          <div className="text-[10px] font-semibold text-gray-500 uppercase">AICF Seeded Players</div>
          <div className="text-xl font-bold text-[#D4A72C] mt-0.5">
            {allPlayers.filter(p => p.seed !== undefined).length} Seeded
          </div>
        </div>
      </div>

      {/* Directory Table Card */}
      <div className="bg-white p-4 rounded-2xl border border-gray-200/80 shadow-xs space-y-3">
        
        <div className="relative w-full sm:w-72">
          <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
            placeholder="Search players by name, club or city..."
            className="w-full text-xs pl-9 pr-3 py-2 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
          />
        </div>

        <div className="overflow-x-auto rounded-xl border border-gray-100">
          <table className="w-full text-left text-xs">
            <thead className="bg-gray-50 text-gray-600 font-semibold border-b border-gray-200">
              <tr>
                <th className="px-4 py-3">Player Details</th>
                <th className="px-3 py-3">Club & City</th>
                <th className="px-3 py-3">Rating / Seed</th>
                <th className="px-3 py-3">Contact Email</th>
                <th className="px-3 py-3">Phone</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {filteredPlayers.length === 0 ? (
                <tr>
                  <td colSpan={6} className="text-center py-10 text-gray-500">
                    <Users className="w-8 h-8 mx-auto text-gray-300 mb-2" />
                    No players found in database.
                  </td>
                </tr>
              ) : (
                filteredPlayers.map((player) => (
                  <tr key={player.id} className="hover:bg-gray-50/80 transition-colors">
                    <td className="px-4 py-3">
                      <div className="font-bold text-gray-900">{player.name}</div>
                      <div className="text-[10px] text-gray-400 font-mono">ID: {player.id}</div>
                    </td>
                    
                    <td className="px-3 py-3">
                      <div className="flex items-center gap-1">
                        <Building className="w-3 h-3 text-gray-400 shrink-0" />
                        <span>{player.club || 'Independent'}</span>
                      </div>
                      <div className="text-[10px] text-gray-500 flex items-center gap-1">
                        <MapPin className="w-3 h-3 text-gray-400 shrink-0" />
                        <span>{player.city || 'Pune'}</span>
                      </div>
                    </td>

                    <td className="px-3 py-3">
                      <div className="font-bold text-emerald-800 flex items-center gap-1">
                        <Flame className="w-3.5 h-3.5 text-amber-500" />
                        <span>{player.rating || 1500} pts</span>
                      </div>
                      {player.seed !== undefined && (
                        <div className="text-[10px] text-[#D4A72C] font-semibold flex items-center gap-0.5">
                          <Award className="w-3 h-3" />
                          <span>AICF Seed #{player.seed}</span>
                        </div>
                      )}
                    </td>

                    <td className="px-3 py-3 text-gray-600">
                      <div className="flex items-center gap-1.5">
                        <Mail className="w-3.5 h-3.5 text-gray-400" />
                        <span className="truncate max-w-[150px]">{player.email || 'N/A'}</span>
                      </div>
                    </td>

                    <td className="px-3 py-3 text-gray-600">
                      {player.phone ? (
                        <div className="flex items-center gap-1">
                          <Phone className="w-3.5 h-3.5 text-gray-400" />
                          <span>{player.phone}</span>
                        </div>
                      ) : (
                        <span className="text-gray-400">N/A</span>
                      )}
                    </td>

                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end space-x-1.5">
                        <button
                          onClick={() => handleOpenEdit(player)}
                          className="p-1.5 text-gray-600 hover:text-emerald-700 hover:bg-emerald-50 rounded-lg transition-colors"
                          title="Edit profile details"
                        >
                          <Edit3 className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => handleDelete(player.id, player.name)}
                          className="p-1.5 text-gray-600 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                          title="Delete player account"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

      </div>

      {/* Add / Edit Profile Form Modal */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 overflow-y-auto bg-black/60 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-3xl max-w-md w-full p-6 shadow-2xl border border-gray-100 relative">
            
            <button
              onClick={() => setIsModalOpen(false)}
              className="absolute top-4 right-4 text-gray-400 hover:text-gray-600 p-1.5 rounded-lg hover:bg-gray-100"
            >
              <X className="w-5 h-5" />
            </button>

            <h3 className="font-serif font-bold text-gray-900 text-base mb-4">
              {editingPlayer ? 'Edit Player Profile' : 'Create Player Profile'}
            </h3>

            <form onSubmit={handleSubmit} className="space-y-4 text-xs">
              
              <div>
                <label className="block font-bold text-gray-700 mb-1">Full Name *</label>
                <input
                  type="text"
                  value={name}
                  onChange={e => setName(e.target.value)}
                  required
                  placeholder="e.g. Ramesh Kumar"
                  className="w-full p-2.5 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                />
              </div>

              <div>
                <label className="block font-bold text-gray-700 mb-1">Email Address</label>
                <input
                  type="email"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  placeholder="ramesh@sports.in"
                  className="w-full p-2.5 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block font-bold text-gray-700 mb-1">Rating Points</label>
                  <input
                    type="number"
                    value={rating}
                    onChange={e => setRating(Number(e.target.value))}
                    min={100}
                    max={3000}
                    required
                    className="w-full p-2.5 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                  />
                </div>

                <div>
                  <label className="block font-bold text-gray-700 mb-1">AICF Seed (Optional)</label>
                  <input
                    type="number"
                    value={seed || ''}
                    onChange={e => setSeed(e.target.value ? Number(e.target.value) : undefined)}
                    min={1}
                    max={128}
                    placeholder="None"
                    className="w-full p-2.5 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block font-bold text-gray-700 mb-1">Club / Academy</label>
                  <input
                    type="text"
                    value={club}
                    onChange={e => setClub(e.target.value)}
                    placeholder="e.g. Deccan Gymkhana"
                    className="w-full p-2.5 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                  />
                </div>

                <div>
                  <label className="block font-bold text-gray-700 mb-1">City</label>
                  <input
                    type="text"
                    value={city}
                    onChange={e => setCity(e.target.value)}
                    required
                    className="w-full p-2.5 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                  />
                </div>
              </div>

              <div>
                <label className="block font-bold text-gray-700 mb-1">Contact Phone</label>
                <input
                  type="tel"
                  value={phone}
                  onChange={e => setPhone(e.target.value)}
                  placeholder="e.g. 9822012345"
                  className="w-full p-2.5 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                />
              </div>

              {editingPlayer && (
                <div className="p-2.5 bg-yellow-50 border border-yellow-100 text-yellow-900 rounded-xl flex items-start gap-2 text-[10px]">
                  <ShieldAlert className="w-4 h-4 text-yellow-600 shrink-0 mt-0.5" />
                  <span>
                    Note: Editing details here updates their profile immediately, allowing automated match schedules to sync correctly.
                  </span>
                </div>
              )}

              {/* Actions */}
              <div className="pt-2 flex justify-end space-x-2">
                <button
                  type="button"
                  onClick={() => setIsModalOpen(false)}
                  className="px-4 py-2 bg-gray-50 border border-gray-200 rounded-xl text-gray-600 hover:bg-gray-100"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 bg-[#0B5D3B] hover:bg-[#094e32] text-white font-bold rounded-xl shadow-sm flex items-center gap-1.5"
                >
                  <Check className="w-4 h-4 text-[#D4A72C]" />
                  <span>{editingPlayer ? 'Update Profile' : 'Create Profile'}</span>
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

    </div>
  );
};
