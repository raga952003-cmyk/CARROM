import React, { useState } from 'react';
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
  ChevronRight
} from 'lucide-react';
import { Tournament, Match } from '../../types/tournament';
import { useTournament } from '../../context/TournamentContext';
import { RegistrationFormModal } from './RegistrationFormModal';
import { FixtureScheduleView } from '../admin/FixtureScheduleView';
import { LiveMatchController } from '../admin/LiveMatchController';
import { StandingsSections } from '../common/StandingsSections';
import { KnockoutBracketView } from '../common/KnockoutBracketView';

export const PlayerDashboard: React.FC = () => {
  const { 
    tournaments, 
    activeTournamentId, 
    setActiveTournamentId,
    activeMatch,
    setActiveMatch,
    currentUser
  } = useTournament();

  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'registration_open' | 'ongoing' | 'completed'>('all');
  const [categoryFilter, setCategoryFilter] = useState<'all' | 'singles' | 'doubles'>('all');

  const [isRegisterModalOpen, setIsRegisterModalOpen] = useState(false);
  const [selectedTournamentForReg, setSelectedTournamentForReg] = useState<Tournament | null>(null);

  // Sub-view in active tournament
  const [activeTab, setActiveTab] = useState<'my_matches' | 'schedule' | 'standings' | 'knockout' | 'poster'>('my_matches');

  const currentTournament = tournaments.find(t => t.id === activeTournamentId) || tournaments[0];

  // Check if current user is registered in current tournament
  const userRegistration = currentTournament?.registrations?.find(r => 
    (currentUser?.id && r.player?.id === currentUser.id) || 
    (currentUser?.name && r.player?.name.toLowerCase() === currentUser.name.toLowerCase()) ||
    (currentUser?.id && r.team?.player1?.id === currentUser.id) ||
    (currentUser?.id && r.team?.player2?.id === currentUser.id)
  );

  // Find all matches involving current user in the active tournament
  const myMatches = currentTournament?.matches?.filter(m => 
    (currentUser?.id && m.player1Id === currentUser.id) || 
    (currentUser?.id && m.player2Id === currentUser.id) ||
    (currentUser?.name && m.player1Name.toLowerCase().includes(currentUser.name.toLowerCase())) ||
    (currentUser?.name && m.player2Name.toLowerCase().includes(currentUser.name.toLowerCase()))
  ) || [];

  // Next upcoming match
  const nextMatch = myMatches.find(m => m.status === 'live' || m.status === 'scheduled');

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
                    Opponent: <strong>{nextMatch.player1Name.includes(currentUser?.name || '') ? nextMatch.player2Name : nextMatch.player1Name}</strong> · Scheduled: {nextMatch.scheduledTime}
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
                const isRegOpen = t.status === 'registration_open';
                const isOngoing = t.status === 'ongoing';
                const isUserRegistered = t.registrations?.some(r => 
                  (currentUser?.id && r.player?.id === currentUser.id) || 
                  (currentUser?.name && r.player?.name.toLowerCase() === currentUser.name.toLowerCase()) ||
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
                          {t.status.replace('_', ' ')}
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
                      {isRegOpen ? (
                        isUserRegistered ? (
                          <span className="px-3 py-1.5 bg-emerald-50 text-emerald-800 text-xs font-bold rounded-xl border border-emerald-200 flex items-center gap-1">
                            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />
                            <span>Registered</span>
                          </span>
                        ) : (
                          <button
                            onClick={(e) => handleOpenRegistration(t, e)}
                            className="px-3.5 py-1.5 bg-[#0B5D3B] hover:bg-[#08472d] text-white text-xs font-bold rounded-xl shadow-xs transition-colors flex items-center gap-1.5"
                          >
                            <UserCheck className="w-3.5 h-3.5 text-[#D4A72C]" />
                            <span>Register Now</span>
                          </button>
                        )
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

                {currentTournament.status === 'registration_open' && (
                  userRegistration ? (
                    <div className="px-4 py-2 bg-emerald-950/40 text-emerald-300 text-xs font-bold rounded-xl border border-emerald-600/30 flex items-center gap-1.5 shrink-0">
                      <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                      <span>Registered (Approved)</span>
                    </div>
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
                        {currentTournament.status === 'registration_open' && (
                          <button
                            onClick={() => handleOpenRegistration(currentTournament)}
                            className="px-4 py-2 bg-[#0B5D3B] text-white text-xs font-bold rounded-xl shadow-xs hover:bg-[#08472d]"
                          >
                            Register for {currentTournament.name}
                          </button>
                        )}
                      </div>
                    ) : (
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {myMatches.map(m => (
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

                            <div className="flex items-center justify-between text-[11px] text-gray-500 pt-2 border-t border-gray-100">
                              <span>{m.scheduledTime}</span>
                              <span className="text-[#0B5D3B] font-bold flex items-center gap-0.5">
                                <span>Follow Board Live</span>
                                <ArrowRight className="w-3 h-3" />
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* Schedule Tab */}
                {activeTab === 'schedule' && (
                  <FixtureScheduleView
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
                    
                    {/* Poster Card */}
                    <div className="bg-[#0B5D3B] text-white p-6 rounded-3xl shadow-xl border-4 border-[#D4A72C]/40 space-y-4">
                      <div className="inline-block px-3 py-1 rounded-full bg-[#D4A72C] text-[#202522] text-[10px] font-bold uppercase tracking-wider">
                        {currentTournament.posterConfig?.badgeText || 'OFFICIAL 2026 CHAMPIONSHIP'}
                      </div>

                      <h3 className="font-serif font-bold text-2xl text-white">
                        {currentTournament.name}
                      </h3>

                      <p className="text-xs italic text-amber-200">
                        "{currentTournament.posterConfig?.tagline || 'Strike with Precision. Reign Supreme on the Board.'}"
                      </p>

                      <div className="bg-black/20 p-3.5 rounded-xl text-xs space-y-1.5 border border-white/10">
                        <div><strong>Venue:</strong> {currentTournament.venue} ({currentTournament.city})</div>
                        <div><strong>Dates:</strong> {currentTournament.tournamentStartDate} to {currentTournament.tournamentEndDate}</div>
                        <div><strong>Prize Pool:</strong> <span className="text-[#D4A72C] font-bold">{currentTournament.prizePool}</span></div>
                        <div><strong>Entry Fee:</strong> ₹{currentTournament.entryFee}</div>
                      </div>

                      <div className="text-[11px] text-emerald-200">
                        Official Synco & Siscaa Boards · All-India Federation Standards
                      </div>
                    </div>

                    {/* Rules Overview */}
                    <div className="bg-white p-6 rounded-3xl border border-gray-200 shadow-xs space-y-3 text-xs">
                      <h4 className="font-serif font-bold text-gray-900 text-base">
                        Tournament & Scoring Regulations
                      </h4>
                      <ul className="space-y-2 text-gray-600 list-disc list-inside">
                        <li>Each match is conducted on official federation boards with a {currentTournament.rules.matchDurationMinutes}-minute timer limit.</li>
                        <li>Queen must be covered by a carrom coin on the same or immediate consecutive turn (+{currentTournament.rules.queenPoints} pts).</li>
                        <li>Win awards <strong>{currentTournament.rules.pointsForWin} points</strong>, Draw awards <strong>{currentTournament.rules.pointsForDraw} point</strong>, Loss awards <strong>{currentTournament.rules.pointsForLoss} points</strong>.</li>
                        <li>Strict {currentTournament.rules.restTimeMinutes}-minute rest period is guaranteed between back-to-back player rounds.</li>
                        <li>Final standings are evaluated deterministically: Points &gt; Board Difference &gt; Net Score Difference.</li>
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
