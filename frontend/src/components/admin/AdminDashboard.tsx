import React, { useCallback, useEffect, useState } from 'react';
import { 
  Plus, 
  Trophy, 
  Calendar, 
  Users, 
  Play, 
  Sparkles, 
  Layers, 
  Clock, 
  MapPin, 
  ArrowRight, 
  CheckCircle2, 
  Palette, 
  Share2, 
  Lock, 
  Sliders, 
  Activity,
  Award,
  Edit3,
  Trash2
} from 'lucide-react';
import { Tournament, Match } from '../../types/tournament';
import { useTournament } from '../../context/TournamentContext';
import { CreateTournamentModal } from './CreateTournamentModal';
import { EditTournamentModal } from './EditTournamentModal';
import { PosterGeneratorModal } from './PosterGeneratorModal';
import { RegistrationManager } from './RegistrationManager';
import { FixtureScheduleView } from './FixtureScheduleView';
import { LiveMatchController } from './LiveMatchController';
import { MatchTossControl } from './MatchTossControl';
import { ScoringRulesSettings, ScoringRules, defaultScoringRules } from './ScoringRulesSettings';
import { StandingsSections } from '../common/StandingsSections';
import { OperationsBar } from './OperationsBar';
import { KnockoutBracketView } from '../common/KnockoutBracketView';
import { ManagePlayersTab } from './ManagePlayersTab';
import { useNotify } from '../../context/NotificationContext';
import { accessService, PERMISSIVE_ACCESS, TournamentAccess } from '../../services/accessService';
import { TournamentAccessPanel } from './TournamentAccessPanel';

export const AdminDashboard: React.FC = () => {
  const { 
    tournaments, 
    activeTournamentId, 
    setActiveTournamentId,
    activeMatch,
    setActiveMatch,
    role,
    publishTournament,
    updateTournament,
    deleteTournament
  } = useTournament();

  const notify = useNotify();

  // What this admin may actually do here. Asked once per tournament so the
  // screen can hide controls that would only ever come back 403 -- a refused
  // request is written to the browser console by the network layer itself,
  // before any JavaScript can intervene, so the only way to be rid of that
  // line is not to send the request.
  const [access, setAccess] = useState<TournamentAccess>(PERMISSIVE_ACCESS);
  const [busy, setBusy] = useState(false);

  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [isPosterModalOpen, setIsPosterModalOpen] = useState(false);
  const [selectedTournamentForPoster, setSelectedTournamentForPoster] = useState<Tournament | null>(null);

  // Tournament workspace sub-tab
  const [activeTab, setActiveTab] = useState<'fixtures' | 'registrations' | 'standings' | 'knockout' | 'overview' | 'players'>('fixtures');

  const currentTournament = tournaments.find(t => t.id === activeTournamentId) || tournaments[0];

  // Re-asked whenever the selected tournament changes. A failure here must not
  // lock the operator out of their own screen, so the assumption on error is
  // permissive and the server still has the final say on every action.
  const refreshAccess = useCallback(() => {
    const id = currentTournament?.id;
    if (!id) { setAccess(PERMISSIVE_ACCESS); return; }
    accessService.myAccessFor(id)
      .then(setAccess)
      .catch(() => setAccess(PERMISSIVE_ACCESS));
  }, [currentTournament?.id]);

  useEffect(() => { refreshAccess(); }, [refreshAccess]);

  const removeTournament = async () => {
    if (!currentTournament || busy) return;
    const ok = window.confirm(
      `Are you sure you want to delete the tournament "${currentTournament.name}"? ` +
      `This action CANNOT be undone and will delete all board matches and registered players!`
    );
    if (!ok) return;
    setBusy(true);
    try {
      await deleteTournament(currentTournament.id);
      notify.success(`Deleted "${currentTournament.name}".`);
    } catch (e) {
      // Previously this rejection escaped into the void: a red console line and
      // a button that appeared to do nothing.
      notify.report(e, 'Could not delete the tournament.');
    } finally {
      setBusy(false);
    }
  };

  const publish = async () => {
    if (!currentTournament || busy) return;
    setBusy(true);
    try {
      await publishTournament(currentTournament.id);
    } catch (e) {
      notify.report(e, 'Could not publish the tournament.');
    } finally {
      setBusy(false);
    }
  };

  const [isEditingRules, setIsEditingRules] = useState(false);
  const [scoringForm, setScoringForm] = useState<ScoringRules>(defaultScoringRules);
  const [rulesForm, setRulesForm] = useState({
    pointsForWin: 2,
    pointsForDraw: 1,
    maxBoardsPerMatch: 3,
    targetScore: 29,
    queenPoints: 3,
    matchDurationMinutes: 30,
    restTimeMinutes: 10,
    venue: '',
    numberOfBoards: 8,
    entryFee: 0,
    registrationStartDate: '',
    registrationEndDate: '',
    tournamentStartDate: '',
    tournamentEndDate: ''
  });

  const startEditing = () => {
    if (currentTournament) {
      setRulesForm({
        pointsForWin: currentTournament.rules.pointsForWin || 2,
        pointsForDraw: currentTournament.rules.pointsForDraw || 1,
        maxBoardsPerMatch: currentTournament.rules.maxBoardsPerMatch || 3,
        targetScore: currentTournament.rules.targetScore || 29,
        queenPoints: currentTournament.rules.queenPoints || 3,
        matchDurationMinutes: currentTournament.rules.matchDurationMinutes || 30,
        restTimeMinutes: currentTournament.rules.restTimeMinutes || 10,
        venue: currentTournament.venue || '',
        numberOfBoards: currentTournament.numberOfBoards || 8,
        entryFee: currentTournament.entryFee || 0,
        registrationStartDate: currentTournament.registrationStartDate || '',
        registrationEndDate: currentTournament.registrationEndDate || '',
        tournamentStartDate: currentTournament.tournamentStartDate || '',
        tournamentEndDate: currentTournament.tournamentEndDate || ''
      });
      const r: any = currentTournament.rules || {};
      setScoringForm({
        // A tournament created before this setting existed has no value, and
        // its confirmed results were decided under the old model.
        scoringMode: r.scoringMode || 'classic',
        numberOfSets: r.numberOfSets ?? 1,
        boardsPerSet: r.boardsPerSet ?? (r.maxBoardsPerMatch || 8),
        coinValue: r.coinValue ?? 1,
        setWinnerRule: r.setWinnerRule || 'total_points',
        boardEntryMode: r.boardEntryMode || 'simple',
        coinsPerSide: r.coinsPerSide ?? 9,
        queenPoints: r.queenPoints ?? 3,
        queenMustBeCovered: r.queenMustBeCovered !== false,
        queenAwardTo: r.queenAwardTo || 'coverer',
        tieBreak: r.tieBreak || 'additional_board',
      });
      setIsEditingRules(true);
    }
  };

  const saveRulesChanges = async () => {
    if (currentTournament) {
      const updates = {
        venue: rulesForm.venue,
        numberOfBoards: rulesForm.numberOfBoards,
        entryFee: rulesForm.entryFee,
        registrationStartDate: rulesForm.registrationStartDate,
        registrationEndDate: rulesForm.registrationEndDate,
        tournamentStartDate: rulesForm.tournamentStartDate,
        tournamentEndDate: rulesForm.tournamentEndDate,
        rules: {
          ...currentTournament.rules,
          pointsForWin: rulesForm.pointsForWin,
          pointsForDraw: rulesForm.pointsForDraw,
          maxBoardsPerMatch: rulesForm.maxBoardsPerMatch,
          targetScore: rulesForm.targetScore,
          queenPoints: rulesForm.queenPoints,
          matchDurationMinutes: rulesForm.matchDurationMinutes,
          restTimeMinutes: rulesForm.restTimeMinutes,
          ...scoringForm
        }
      };

      await updateTournament(currentTournament.id, updates);
      setIsEditingRules(false);
    }
  };

  // Matches whose toss step is finished in this session, so a database
  // without migration 004 does not send the umpire round the wizard again.
  const [tossedIds, setTossedIds] = useState<string[]>([]);
  const [matchNotice, setMatchNotice] = useState('');
  const setTossDone = (id: string) => setTossedIds(prev => prev.includes(id) ? prev : [...prev, id]);
  const needsToss = (m: Match) =>
    role === 'admin' &&
    m.status === 'scheduled' &&
    !m.resultConfirmed &&
    !m.tossRecordedAt &&
    !tossedIds.includes(m.id);

  const liveActiveMatch = activeMatch && currentTournament 
    ? currentTournament.matches.find(m => m.id === activeMatch.id) || activeMatch
    : null;

  const handleOpenPoster = (t: Tournament, e: React.MouseEvent) => {
    e.stopPropagation();
    setSelectedTournamentForPoster(t);
    setIsPosterModalOpen(true);
  };

  return (
    <div id="admin-dashboard-root" className="space-y-6">
      
      {/* If a match is currently opened in the controller, show LiveMatchController */}
      {liveActiveMatch && currentTournament ? (
        // A match that has not been started yet begins with the toss; once it
        // is live the umpire goes straight to scoring.
        needsToss(liveActiveMatch) ? (
          <MatchTossControl
            tournament={currentTournament}
            match={liveActiveMatch}
            onBack={() => setActiveMatch(null)}
            onStarted={(warning) => {
              setMatchNotice(warning || '');
              setTossDone(liveActiveMatch.id);
            }}
          />
        ) : (
          <LiveMatchController
            tournament={currentTournament}
            match={liveActiveMatch}
            onBack={() => setActiveMatch(null)}
            notice={matchNotice}
            onDismissNotice={() => setMatchNotice('')}
          />
        )
      ) : (
        <>
          {/* Tournament Selection & Creation Bar */}
          <div className="bg-white p-5 rounded-2xl border border-gray-200/80 shadow-xs flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div>
              <p className="text-[10px] sm:text-xs font-bold text-[#0B5D3B] uppercase tracking-widest">
                Tournament Command Center
              </p>
              <h2 className="text-xl sm:text-2xl font-bold text-gray-900 mt-0.5">
                Championship Management Hub
              </h2>
              <p className="text-xs text-gray-500 mt-0.5">
                Select an active event to manage registrations, generate AI posters, configure boards, or run live match control.
              </p>
            </div>

            <div className="flex items-center space-x-2 shrink-0">
              <button
                id="create-tournament-btn"
                onClick={() => setIsCreateModalOpen(true)}
                className="px-5 py-2.5 bg-[#D4A72C] hover:opacity-90 text-[#0B5D3B] text-xs font-black rounded-xl shadow-md transition-all flex items-center gap-2"
              >
                <Plus className="w-4 h-4 text-[#0B5D3B]" />
                <span>Create Tournament</span>
              </button>
            </div>
          </div>

          {/* Horizontal Tournament Cards Selector */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {tournaments.map((t) => {
              const isSelected = t.id === activeTournamentId;
              const liveCount = t.matches.filter(m => m.status === 'live').length;

              return (
                <div
                  key={t.id}
                  onClick={() => setActiveTournamentId(t.id)}
                  className={`bg-white rounded-2xl p-5 border transition-all cursor-pointer relative shadow-xs hover:shadow-md ${
                    isSelected
                      ? 'border-[#0B5D3B] ring-2 ring-[#0B5D3B]/20 bg-emerald-50/20'
                      : 'border-gray-200/80 hover:border-gray-300'
                  }`}
                >
                  {/* Status & Category Pills */}
                  <div className="flex items-center justify-between mb-3">
                    <span className={`px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider ${
                      t.status === 'ongoing' ? 'bg-orange-100 text-orange-800' :
                      t.status === 'registration_open' ? 'bg-emerald-100 text-emerald-800' :
                      t.status === 'completed' ? 'bg-gray-100 text-gray-700' :
                      'bg-blue-100 text-blue-800'
                    }`}>
                      {t.status.replace('_', ' ')}
                    </span>

                    <span className="text-[11px] font-bold text-gray-500 uppercase">
                      {t.category} · {t.format.replace('_', ' ')}
                    </span>
                  </div>

                  <h3 className="font-serif font-bold text-base text-gray-900 leading-snug mb-1">
                    {t.name}
                  </h3>

                  <p className="text-xs text-gray-500 line-clamp-2 mb-3">
                    {t.description}
                  </p>

                  {/* Metadata chips */}
                  <div className="grid grid-cols-2 gap-2 text-[11px] text-gray-600 mb-4 bg-gray-50 p-2.5 rounded-xl border border-gray-100">
                    <div className="flex items-center space-x-1.5 truncate">
                      <MapPin className="w-3.5 h-3.5 text-[#0B5D3B] shrink-0" />
                      <span className="truncate">{t.city}</span>
                    </div>
                    <div className="flex items-center space-x-1.5 truncate">
                      <Calendar className="w-3.5 h-3.5 text-[#0B5D3B] shrink-0" />
                      <span className="truncate">{t.tournamentStartDate}</span>
                    </div>
                    <div className="flex items-center space-x-1.5 truncate">
                      <Users className="w-3.5 h-3.5 text-[#0B5D3B] shrink-0" />
                      <span>{t.registrations.length} Players</span>
                    </div>
                    <div className="flex items-center space-x-1.5 truncate">
                      <Trophy className="w-3.5 h-3.5 text-[#D4A72C] shrink-0" />
                      <span className="font-semibold text-gray-900 truncate">{t.prizePool}</span>
                    </div>
                  </div>

                  {/* Quick Card Action Buttons */}
                  <div className="flex items-center justify-between pt-2 border-t border-gray-100">
                    <button
                      onClick={(e) => handleOpenPoster(t, e)}
                      className="text-xs text-[#0B5D3B] hover:text-[#08472d] font-bold flex items-center gap-1 px-2.5 py-1 rounded-lg hover:bg-emerald-50 transition-colors"
                    >
                      <Palette className="w-3.5 h-3.5 text-[#D4A72C]" />
                      <span>AI Poster</span>
                    </button>

                    <div className="flex items-center space-x-1 text-xs font-bold text-[#0B5D3B]">
                      <span>{isSelected ? 'Active Event' : 'Select Workspace'}</span>
                      <ArrowRight className="w-3.5 h-3.5" />
                    </div>
                  </div>

                  {/* Live matches badge if active */}
                  {liveCount > 0 && (
                    <div className="absolute top-2 right-2 w-2.5 h-2.5 rounded-full bg-orange-500 animate-ping" />
                  )}
                </div>
              );
            })}
          </div>

          {/* Active Tournament Detail Workspace */}
          {currentTournament && (
            <div className="bg-white rounded-3xl border border-gray-200/80 shadow-xs overflow-hidden">
              
              {/* Workspace Navigation Header */}
              <div className="px-4 sm:px-6 py-4 sm:py-5 bg-white border-b border-gray-200/80 flex flex-col md:flex-row md:items-center justify-between gap-3 sm:gap-4">
                <div>
                  <div className="flex items-center space-x-2">
                    <span className="text-[10px] font-bold text-[#0B5D3B] uppercase tracking-widest">
                      Active Tournament
                    </span>
                    <span className="text-gray-300">·</span>
                    <span className="text-xs text-gray-500 font-medium">
                      {currentTournament.venue} ({currentTournament.numberOfBoards} Boards)
                    </span>
                  </div>
                  <div className="flex flex-wrap items-center gap-3 mt-1">
                    <h3 className="text-xl sm:text-2xl font-bold text-gray-900">
                      {currentTournament.name}
                    </h3>
                    <span className="px-3 py-0.5 bg-[#2E7D3222] text-[#2E7D32] text-xs font-bold rounded-full border border-[#2E7D32]">
                      {currentTournament.status === 'ongoing' ? 'Ongoing' : currentTournament.status.replace('_', ' ').toUpperCase()}
                    </span>
                  </div>
                </div>

                <div className="flex items-center space-x-3 shrink-0">
                  <button
                    onClick={() => {
                      setSelectedTournamentForPoster(currentTournament);
                      setIsPosterModalOpen(true);
                    }}
                    className="px-3.5 py-2 bg-emerald-50 hover:bg-emerald-100 text-[#0B5D3B] text-xs font-bold rounded-xl border border-emerald-300 flex items-center gap-1.5 transition-colors shadow-xs"
                  >
                    <Palette className="w-3.5 h-3.5 text-[#D4A72C]" />
                    <span>AI Poster</span>
                  </button>

                  <button
                    onClick={() => setIsEditModalOpen(true)}
                    className="px-3.5 py-2 bg-blue-50 hover:bg-blue-100 text-blue-800 text-xs font-bold rounded-xl border border-blue-300 flex items-center gap-1.5 transition-colors shadow-xs"
                  >
                    <Edit3 className="w-3.5 h-3.5 text-blue-600" />
                    <span>Edit Details</span>
                  </button>

                  {/* Hidden rather than disabled when the server would refuse:
                      an admin who cannot delete this tournament has no use for
                      the control, and pressing it would only log a 403. */}
                  {access.canManage && (
                  <button
                    onClick={removeTournament}
                    disabled={busy}
                    className="px-3.5 py-2 bg-red-50 hover:bg-red-100 text-red-800 text-xs font-bold rounded-xl border border-red-300 flex items-center gap-1.5 transition-colors shadow-xs"
                  >
                    <Trash2 className="w-3.5 h-3.5 text-red-600" />
                    <span>Delete</span>
                  </button>
                  )}

                  {currentTournament.status === 'draft' && (
                    <button
                      onClick={publish}
                      disabled={busy}
                      className="px-4 py-2 bg-[#D4A72C] hover:opacity-90 text-[#0B5D3B] text-xs font-black rounded-xl shadow-md transition-colors flex items-center gap-1.5"
                    >
                      <Share2 className="w-3.5 h-3.5" />
                      <span>Publish Tournament</span>
                    </button>
                  )}
                </div>
              </div>

              {/* Sub-Tabs Navigation */}
              <OperationsBar tournament={currentTournament} />

              <div className="px-3 sm:px-6 pt-3 bg-gray-50 border-b border-gray-200 flex space-x-1.5 sm:space-x-2 overflow-x-auto scroll-smooth">
                {[
                  { id: 'fixtures', label: 'Fixtures & Schedule', icon: Calendar, badge: currentTournament.matches.length },
                  { id: 'registrations', label: 'Registrations', icon: Users, badge: currentTournament.registrations.length },
                  { id: 'standings', label: 'Points & Standings', icon: Trophy },
                  { id: 'knockout', label: 'Knockout Bracket', icon: Award },
                  { id: 'players', label: 'Players Directory', icon: Users },
                  { id: 'overview', label: 'Rules & Venue Details', icon: Sliders }
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

              {/* Tab Workspace Content */}
              <div className="p-6 bg-[#F8F6F0]/40">
                {activeTab === 'fixtures' && (
                  <FixtureScheduleView
                    tournament={currentTournament}
                    onOpenMatch={(m) => setActiveMatch(m)}
                  />
                )}

                {activeTab === 'registrations' && (
                  <RegistrationManager tournament={currentTournament} />
                )}

                {activeTab === 'players' && (
                  <ManagePlayersTab />
                )}

                {activeTab === 'standings' && (
                  <StandingsSections tournament={currentTournament} />
                )}

                {activeTab === 'knockout' && (
                  <KnockoutBracketView
                    tournament={currentTournament}
                    onOpenMatch={(m) => setActiveMatch(m)}
                  />
                )}                {activeTab === 'overview' && (
                  <div className="space-y-4">
                    <TournamentAccessPanel
                      tournamentId={currentTournament.id}
                      tournamentName={currentTournament.name}
                      access={access}
                      onChanged={refreshAccess}
                    />
                    {/* Edit mode controls header */}
                    <div className="flex flex-col sm:flex-row sm:justify-end gap-2 bg-white p-3 rounded-2xl border border-gray-200 shadow-2xs">
                      {isEditingRules ? (
                        <>
                          <button
                            onClick={() => setIsEditingRules(false)}
                            className="w-full sm:w-auto px-4 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 text-xs font-bold rounded-xl transition-colors"
                          >
                            Cancel
                          </button>
                          <button
                            onClick={saveRulesChanges}
                            className="w-full sm:w-auto px-4 py-2 bg-[#0B5D3B] hover:bg-[#08472d] text-white text-xs font-bold rounded-xl shadow-md transition-all"
                          >
                            Save Rules & Venue Details
                          </button>
                        </>
                      ) : (
                        <button
                          onClick={startEditing}
                          className="w-full sm:w-auto px-4 py-2 bg-[#0B5D3B] hover:bg-[#08472d] text-white text-xs font-bold rounded-xl shadow-md transition-all"
                        >
                          Edit Rules & Venue Details
                        </button>
                      )}
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
                      
                      {/* Rules Card */}
                      <div className="bg-white p-5 rounded-2xl border border-gray-200 shadow-xs space-y-3">
                        <h4 className="font-serif font-bold text-gray-900 text-sm">
                          Federation Rules Configuration
                        </h4>
                        <div className="space-y-2 text-xs text-gray-600">
                          <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center py-1.5 border-b border-gray-100 gap-1.5 sm:gap-0">
                            <span>Points for Win:</span>
                            {isEditingRules ? (
                              <input
                                type="number"
                                min={1}
                                max={5}
                                value={rulesForm.pointsForWin}
                                onChange={e => setRulesForm({ ...rulesForm, pointsForWin: parseInt(e.target.value) || 2 })}
                                className="w-full sm:w-24 p-1 border border-gray-200 rounded text-left sm:text-right font-bold focus:border-[#0B5D3B] focus:ring-1 focus:ring-[#0B5D3B]"
                              />
                            ) : (
                              <strong className="text-gray-900">{currentTournament.rules.pointsForWin} pts</strong>
                            )}
                          </div>
                          <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center py-1.5 border-b border-gray-100 gap-1.5 sm:gap-0">
                            <span>Points for Draw:</span>
                            {isEditingRules ? (
                              <input
                                type="number"
                                min={0}
                                max={3}
                                value={rulesForm.pointsForDraw}
                                onChange={e => setRulesForm({ ...rulesForm, pointsForDraw: parseInt(e.target.value) || 1 })}
                                className="w-full sm:w-24 p-1 border border-gray-200 rounded text-left sm:text-right font-bold focus:border-[#0B5D3B] focus:ring-1 focus:ring-[#0B5D3B]"
                              />
                            ) : (
                              <strong className="text-gray-900">{currentTournament.rules.pointsForDraw} pt</strong>
                            )}
                          </div>
                          <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center py-1.5 border-b border-gray-100 gap-1.5 sm:gap-0">
                            <span>Max Boards per Match:</span>
                            {isEditingRules ? (
                              <input
                                type="number"
                                min={1}
                                max={15}
                                value={rulesForm.maxBoardsPerMatch}
                                onChange={e => setRulesForm({ ...rulesForm, maxBoardsPerMatch: parseInt(e.target.value) || 3 })}
                                className="w-full sm:w-24 p-1 border border-gray-200 rounded text-left sm:text-right font-bold focus:border-[#0B5D3B] focus:ring-1 focus:ring-[#0B5D3B]"
                              />
                            ) : (
                              <strong className="text-gray-900">{currentTournament.rules.maxBoardsPerMatch} boards</strong>
                            )}
                          </div>
                          <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center py-1.5 border-b border-gray-100 gap-1.5 sm:gap-0">
                            <span>Target Score Cap:</span>
                            {isEditingRules ? (
                              <input
                                type="number"
                                min={1}
                                max={50}
                                value={rulesForm.targetScore}
                                onChange={e => setRulesForm({ ...rulesForm, targetScore: parseInt(e.target.value) || 29 })}
                                className="w-full sm:w-24 p-1 border border-gray-200 rounded text-left sm:text-right font-bold focus:border-[#0B5D3B] focus:ring-1 focus:ring-[#0B5D3B]"
                              />
                            ) : (
                              <strong className="text-gray-900">{currentTournament.rules.targetScore} pts</strong>
                            )}
                          </div>
                          <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center py-1.5 border-b border-gray-100 gap-1.5 sm:gap-0">
                            <span>Queen Value:</span>
                            <strong className="text-gray-900">+{currentTournament.rules.queenPoints ?? 3} pts</strong>
                          </div>
                          <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center py-1.5 gap-1.5 sm:gap-0">
                            <span>Board Scoring:</span>
                            <strong className="text-gray-900">
                              {(currentTournament.rules as any)?.scoringMode === 'remaining_coins'
                                ? "Winner scores the opponent's remaining coins"
                                : 'Each player scores their own coins'}
                            </strong>
                          </div>
                        </div>

                        {isEditingRules && (
                          <div className="pt-1">
                            <ScoringRulesSettings value={scoringForm} onChange={setScoringForm} />
                          </div>
                        )}
                      </div>

                      {/* Schedule & Equipment Card */}
                      <div className="bg-white p-5 rounded-2xl border border-gray-200 shadow-xs space-y-3">
                        <h4 className="font-serif font-bold text-gray-900 text-sm">
                          Venue & Equipment
                        </h4>
                        <div className="space-y-2 text-xs text-gray-600">
                          <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center py-1.5 border-b border-gray-100 gap-1.5 sm:gap-0">
                            <span>Venue Name:</span>
                            {isEditingRules ? (
                              <input
                                type="text"
                                value={rulesForm.venue}
                                onChange={e => setRulesForm({ ...rulesForm, venue: e.target.value })}
                                className="w-full sm:w-36 p-1 border border-gray-200 rounded text-left sm:text-right font-semibold focus:border-[#0B5D3B] focus:ring-1 focus:ring-[#0B5D3B]"
                              />
                            ) : (
                              <strong className="text-gray-900 truncate max-w-[150px]">{currentTournament.venue}</strong>
                            )}
                          </div>
                          <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center py-1.5 border-b border-gray-100 gap-1.5 sm:gap-0">
                            <span>Available Boards:</span>
                            {isEditingRules ? (
                              <input
                                type="number"
                                min={1}
                                max={100}
                                value={rulesForm.numberOfBoards}
                                onChange={e => setRulesForm({ ...rulesForm, numberOfBoards: parseInt(e.target.value) || 8 })}
                                className="w-full sm:w-24 p-1 border border-gray-200 rounded text-left sm:text-right font-bold focus:border-[#0B5D3B] focus:ring-1 focus:ring-[#0B5D3B]"
                              />
                            ) : (
                              <strong className="text-gray-900">{currentTournament.numberOfBoards} Synco Boards</strong>
                            )}
                          </div>
                          <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center py-1.5 border-b border-gray-100 gap-1.5 sm:gap-0">
                            <span>Match Duration:</span>
                            {isEditingRules ? (
                              <input
                                type="number"
                                min={5}
                                max={180}
                                value={rulesForm.matchDurationMinutes}
                                onChange={e => setRulesForm({ ...rulesForm, matchDurationMinutes: parseInt(e.target.value) || 30 })}
                                className="w-full sm:w-24 p-1 border border-gray-200 rounded text-left sm:text-right font-bold focus:border-[#0B5D3B] focus:ring-1 focus:ring-[#0B5D3B]"
                              />
                            ) : (
                              <strong className="text-gray-900">{currentTournament.rules.matchDurationMinutes} min</strong>
                            )}
                          </div>
                          <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center py-1.5 border-b border-gray-100 gap-1.5 sm:gap-0">
                            <span>Rest Time Buffer:</span>
                            {isEditingRules ? (
                              <input
                                type="number"
                                min={0}
                                max={60}
                                value={rulesForm.restTimeMinutes}
                                onChange={e => setRulesForm({ ...rulesForm, restTimeMinutes: parseInt(e.target.value) || 10 })}
                                className="w-full sm:w-24 p-1 border border-gray-200 rounded text-left sm:text-right font-bold focus:border-[#0B5D3B] focus:ring-1 focus:ring-[#0B5D3B]"
                              />
                            ) : (
                              <strong className="text-gray-900">{currentTournament.rules.restTimeMinutes} min</strong>
                            )}
                          </div>
                          <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center py-1.5 border-b border-gray-100 gap-1.5 sm:gap-0">
                            <span>Entry Fee:</span>
                            {isEditingRules ? (
                              <div className="flex items-center space-x-1 w-full sm:w-auto justify-start sm:justify-end">
                                <span className="font-semibold text-gray-500">₹</span>
                                <input
                                  type="number"
                                  min={0}
                                  value={rulesForm.entryFee}
                                  onChange={e => setRulesForm({ ...rulesForm, entryFee: parseFloat(e.target.value) || 0 })}
                                  className="w-full sm:w-24 p-1 border border-gray-200 rounded text-left sm:text-right font-bold focus:border-[#0B5D3B] focus:ring-1 focus:ring-[#0B5D3B]"
                                />
                              </div>
                            ) : (
                              <strong className="text-gray-900">₹{currentTournament.entryFee} / entry</strong>
                            )}
                          </div>
                        </div>
                      </div>

                      {/* Important Dates */}
                      <div className="bg-white p-5 rounded-2xl border border-gray-200 shadow-xs space-y-3">
                        <h4 className="font-serif font-bold text-gray-900 text-sm">
                          Important Milestones
                        </h4>
                        <div className="space-y-2 text-xs text-gray-600">
                          <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center py-1.5 border-b border-gray-100 gap-1.5 sm:gap-0">
                            <span>Registration Opens:</span>
                            {isEditingRules ? (
                              <input
                                type="date"
                                value={rulesForm.registrationStartDate}
                                onChange={e => setRulesForm({ ...rulesForm, registrationStartDate: e.target.value })}
                                className="w-full sm:w-36 p-1 border border-gray-200 rounded text-left sm:text-right font-semibold focus:border-[#0B5D3B] focus:ring-1 focus:ring-[#0B5D3B]"
                              />
                            ) : (
                              <strong className="text-gray-900">{currentTournament.registrationStartDate}</strong>
                            )}
                          </div>
                          <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center py-1.5 border-b border-gray-100 gap-1.5 sm:gap-0">
                            <span>Registration Deadline:</span>
                            {isEditingRules ? (
                              <input
                                type="date"
                                value={rulesForm.registrationEndDate}
                                onChange={e => setRulesForm({ ...rulesForm, registrationEndDate: e.target.value })}
                                className="w-full sm:w-36 p-1 border border-gray-200 rounded text-left sm:text-right font-semibold focus:border-[#0B5D3B] focus:ring-1 focus:ring-[#0B5D3B]"
                              />
                            ) : (
                              <strong className="text-gray-900">{currentTournament.registrationEndDate}</strong>
                            )}
                          </div>
                          <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center py-1.5 border-b border-gray-100 gap-1.5 sm:gap-0">
                            <span>Tournament Starts:</span>
                            {isEditingRules ? (
                              <input
                                type="date"
                                value={rulesForm.tournamentStartDate}
                                onChange={e => setRulesForm({ ...rulesForm, tournamentStartDate: e.target.value })}
                                className="w-full sm:w-36 p-1 border border-gray-200 rounded text-left sm:text-right font-semibold focus:border-[#0B5D3B] focus:ring-1 focus:ring-[#0B5D3B]"
                              />
                            ) : (
                              <strong className="text-gray-900">{currentTournament.tournamentStartDate}</strong>
                            )}
                          </div>
                          <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center py-1.5 border-b border-gray-100 gap-1.5 sm:gap-0">
                            <span>Tournament Concludes:</span>
                            {isEditingRules ? (
                              <input
                                type="date"
                                value={rulesForm.tournamentEndDate}
                                onChange={e => setRulesForm({ ...rulesForm, tournamentEndDate: e.target.value })}
                                className="w-full sm:w-36 p-1 border border-gray-200 rounded text-left sm:text-right font-semibold focus:border-[#0B5D3B] focus:ring-1 focus:ring-[#0B5D3B]"
                              />
                            ) : (
                              <strong className="text-gray-900">{currentTournament.tournamentEndDate}</strong>
                            )}
                          </div>
                        </div>
                      </div>

                    </div>
                  </div>
                )}
              </div>

            </div>
          )}

        </>
      )}

      {/* Create Tournament Modal */}
      <CreateTournamentModal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
      />

      {/* Edit Tournament Modal */}
      {isEditModalOpen && currentTournament && (
        <EditTournamentModal
          isOpen={isEditModalOpen}
          onClose={() => setIsEditModalOpen(false)}
          tournament={currentTournament}
        />
      )}

      {/* Poster Generator Modal */}
      {isPosterModalOpen && selectedTournamentForPoster && (
        <PosterGeneratorModal
          tournament={selectedTournamentForPoster}
          isOpen={isPosterModalOpen}
          onClose={() => {
            setIsPosterModalOpen(false);
            setSelectedTournamentForPoster(null);
          }}
        />
      )}

    </div>
  );
};
