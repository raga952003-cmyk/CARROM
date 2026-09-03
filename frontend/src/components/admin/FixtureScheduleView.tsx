import React, { useState, useMemo, useDeferredValue, useEffect } from 'react';
import { compareMatches } from '../../utils/matchOrder';
import { Sparkles, Calendar, Clock, Layers, Play, Check, RefreshCw, AlertCircle, Flame, CheckCircle2, Grid, List, ShieldCheck, ArrowRight, Send, Eye, Plus, AlertTriangle, Search, X, Loader2 } from 'lucide-react';
import { Tournament, Match } from '../../types/tournament';
import { useTournament } from '../../context/TournamentContext';
import { useNotify } from '../../context/NotificationContext';
import { apiClient } from '../../utils/apiClient';
import { ConfirmationModal } from '../common/ConfirmationModal';
import { MatchTimer } from './MatchTimer';
import { AddMatchModal } from './AddMatchModal';

interface FixtureScheduleViewProps {
  tournament: Tournament;
  onOpenMatch: (match: Match) => void;
}

/**
 * The filters an organiser last had on, per tournament.
 *
 * Opening a match replaces this whole view with the scoring screen, so the
 * view is unmounted and its state goes with it. Every time an organiser
 * searched for a player, opened their fixture, scored it and came back, the
 * search box was empty again and the round filter was back to All Rounds --
 * on every single match of the day.
 *
 * Kept in the module rather than lifted into a prop because both the admin
 * dashboard and the player dashboard render this view, and neither of them
 * has any other reason to know what is typed in its search box. It lives as
 * long as the page does, which is exactly as long as it is useful.
 */
const lastFilters = new Map<string, {
  round: string;
  search: string;
  category: 'all' | 'singles' | 'doubles';
}>();

/**
 * Forget them. Called on sign-out: this map outlives the session, so without
 * it the next person to sign in on the same device found the previous one's
 * search term already in the box.
 */
export function forgetFixtureFilters(): void {
  lastFilters.clear();
}

/** The writes this screen makes, named so the button that started one can show it. */
type ActionKey = 'generate' | 'schedule' | 'publish' | 'reschedule';

export const FixtureScheduleView: React.FC<FixtureScheduleViewProps> = ({
  tournament,
  onOpenMatch
}) => {
  const {
    generateFixturesForTournament,
    generateScheduleForTournament,
    publishScheduleForTournament,
    refreshTournaments,
    role
  } = useTournament();
  const notify = useNotify();

  const [viewMode, setViewMode] = useState<'rounds' | 'boards'>('rounds');
  // A one-minute turnaround is a real option: on a small draw the boards
  // are free again as soon as the previous pair stand up.
  const [restMinutes, setRestMinutes] = useState(1);
  const [isPublishModalOpen, setIsPublishModalOpen] = useState(false);
  const [isRegenerateModalOpen, setIsRegenerateModalOpen] = useState(false);
  const [isRescheduleModalOpen, setIsRescheduleModalOpen] = useState(false);
  const [isAddMatchOpen, setIsAddMatchOpen] = useState(false);
  // One write at a time. Generating deletes the existing draw before writing
  // the new one, so a second click while the first is running erases what it
  // has just written; scheduling and publishing are cheaper to repeat but no
  // better for it. `busy` names the action so its button can show a spinner.
  const [busy, setBusy] = useState<ActionKey | null>(null);
  const [actionError, setActionError] = useState('');

  const runAction = async (key: ActionKey, fn: () => Promise<void>, fallback: string): Promise<boolean> => {
    if (busy) return false;
    setBusy(key);
    setActionError('');
    try {
      await fn();
      return true;
    } catch (e) {
      // The toast still fires; this keeps the sentence beside the buttons.
      setActionError(notify.report(e, fallback));
      return false;
    } finally {
      setBusy(null);
    }
  };

  const runGenerate = () => runAction('generate',
    () => generateFixturesForTournament(tournament.id), 'Could not generate fixtures.');

  // Regenerating replaces the draw and everything scored on it. The server
  // refuses that unless told the organiser has agreed, and the confirmation
  // modal is where they agree, so this is the one call that passes force.
  const runRegenerate = () => runAction('generate', async () => {
    await apiClient.post(`/tournaments/${tournament.id}/fixtures?force=true`, {});
    await refreshTournaments();
  }, 'Could not regenerate the fixtures.');

  const runSchedule = () => runAction('schedule',
    () => generateScheduleForTournament(tournament.id, restMinutes), 'Could not build the schedule.');

  const runPublish = () => runAction('publish',
    () => publishScheduleForTournament(tournament.id), 'Could not publish the schedule.');

  // Changing a schedule that is already out. The schedule endpoint does not
  // care whether the schedule was published, and publishing again only
  // notifies the participants again, so running the two in turn is how a
  // published schedule is changed. Results are not touched.
  const runReschedule = () => runAction('reschedule', async () => {
    await generateScheduleForTournament(tournament.id, restMinutes);
    await publishScheduleForTournament(tournament.id);
  }, 'Could not reschedule.');

  const [selectedRoundFilter, setSelectedRoundFilter] = useState<string>(
    () => lastFilters.get(tournament.id)?.round ?? 'all');
  const [playerSearch, setPlayerSearch] = useState(
    () => lastFilters.get(tournament.id)?.search ?? '');


  const allMatches = tournament.matches || [];

  // Everything below is derived from the draw, and the draw only changes when
  // the server says so — but this component re-renders on every keystroke in
  // the search box below, and on every realtime change anywhere in the app.
  // Unmemoised, one keystroke rescanned all 1520 boards of a 190-fixture draw,
  // rebuilt the board timeline, and counted every round against every match.
  // That is the lag an organiser feels while typing a name.

  // What a redraw would throw away, counted the way the server counts it: any
  // board that is not pending, or carries a score, is play.
  const { playedBoards, confirmedMatches } = useMemo(() => ({
    playedBoards: allMatches.reduce((n, m) =>
      n + (m.boards || []).filter(b => b.status !== 'pending' || b.player1Score || b.player2Score).length, 0),
    confirmedMatches: allMatches.filter(m => m.resultConfirmed).length,
  }), [allMatches]);

  // Singles and doubles are separate competitions inside one tournament, so
  // the fixture list is filtered by category before anything else.
  const categoriesPresent = useMemo(() => Array.from(
    new Set(allMatches.map(m => m.type).filter(Boolean))
  ) as ('singles' | 'doubles')[], [allMatches]);
  const showCategoryTabs = categoriesPresent.length > 1;

  const [categoryFilter, setCategoryFilter] = React.useState<'all' | 'singles' | 'doubles'>(
    () => lastFilters.get(tournament.id)?.category ?? 'all');
  // Remembered as they change, so coming back from a match lands where the
  // organiser left off.
  useEffect(() => {
    lastFilters.set(tournament.id, {
      round: selectedRoundFilter,
      search: playerSearch,
      category: categoryFilter,
    });
  }, [tournament.id, selectedRoundFilter, playerSearch, categoryFilter]);

  const matches = useMemo(() => categoryFilter === 'all'
    ? allMatches
    : allMatches.filter(m => m.type === categoryFilter), [allMatches, categoryFilter]);
  const hasMatches = allMatches.length > 0;
  const isScheduled = tournament.status === 'scheduled' || tournament.status === 'ongoing' || tournament.status === 'completed';

  // Group matches by round, within the selected category, and count each one
  // once. The tabs used to re-filter the whole draw per round, which is every
  // fixture read nineteen times over to print nineteen small numbers.
  const { rounds, roundCounts } = useMemo(() => {
    const counts = new Map<string, number>();
    for (const m of matches) counts.set(m.roundName, (counts.get(m.roundName) || 0) + 1);
    return { rounds: Array.from(counts.keys()), roundCounts: counts };
  }, [matches]);

  // Find a player's or a team's fixtures.
  //
  // With 190 fixtures across nineteen rounds, "when do I play, and who?" was a
  // question you could only answer by reading every card. Matching on the two
  // side names covers doubles as well, because a doubles fixture carries the
  // team's name rather than the two people in it.
  // The box keeps up with the typist; the list of 190 cards catches up a beat
  // later. Without this every character re-rendered the whole draw before the
  // next one could be accepted.
  const needle = useDeferredValue(playerSearch).trim().toLowerCase();

  // A search is a question about one person, so the round tabs stop applying:
  // their next match is very unlikely to be in the round being looked at.
  const displayedMatches = useMemo(() => {
    if (needle) {
      return matches.filter(m =>
        (m.player1Name || '').toLowerCase().includes(needle) ||
        (m.player2Name || '').toLowerCase().includes(needle));
    }
    return selectedRoundFilter === 'all'
      ? matches
      : matches.filter(m => m.roundName === selectedRoundFilter);
  }, [matches, needle, selectedRoundFilter]);

  // While a search is running the displayed set IS the search result — the
  // round filter is deliberately ignored — so the summary line below reads it
  // under the name that says what it is.
  const searchedMatches = displayedMatches;

  // Group matches by board for board timeline
  const boardMap = useMemo(() => {
    const map: Map<number, Match[]> = new Map();
    for (let b = 1; b <= tournament.numberOfBoards; b++) map.set(b, []);
    matches.forEach(m => {
      const list = map.get(m.boardNumber) || [];
      list.push(m);
      map.set(m.boardNumber, list);
    });
    return map;
  }, [matches, tournament.numberOfBoards]);

  return (
    <div id="fixture-schedule-view" className="space-y-5">
      
      {/* Control Banner Card */}
      <div className="bg-white p-5 rounded-2xl border border-gray-200/80 shadow-xs flex flex-col lg:flex-row lg:items-center justify-between gap-4">
        <div>
          <div className="flex items-center space-x-2">
            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-[#0B5D3B] text-white uppercase tracking-wider">
              <Sparkles className="w-3 h-3 text-[#D4A72C]" />
              Automated Scheduler
            </span>
            <span className="text-xs text-gray-500">
              {tournament.numberOfBoards} Synco Championship Boards · {tournament.rules.matchDurationMinutes} min matches
            </span>
          </div>

          <h3 className="text-lg font-serif font-bold text-gray-900 mt-1">
            Fixtures & Conflict-Free Schedule Engine
          </h3>
          <p className="text-xs text-gray-600">
            Generates all tournament pairings and assigns conflict-free boards, dates, and rest periods with zero manual effort.
          </p>
        </div>

        {/* Action Controls */}
        <div className="flex flex-wrap items-center gap-2">
          {role === 'admin' && (
            <>
              {/* Rest buffer slider/selector */}
              <div className="flex items-center bg-gray-50 px-2.5 py-1.5 rounded-xl border border-gray-200 text-xs">
                <span className="text-gray-500 mr-2 font-medium">Rest Buffer:</span>
                <select
                  value={restMinutes}
                  onChange={e => setRestMinutes(parseInt(e.target.value))}
                  className="bg-transparent font-bold text-gray-800 focus:outline-hidden"
                >
                  <option value={1}>1 min</option>
                  <option value={2}>2 mins</option>
                  <option value={5}>5 mins</option>
                  <option value={10}>10 mins</option>
                  <option value={15}>15 mins</option>
                  <option value={20}>20 mins</option>
                </select>
              </div>

              {!hasMatches ? (
                <button
                  id="generate-fixtures-btn"
                  onClick={runGenerate}
                  disabled={!!busy}
                  className="px-4 py-2 bg-[#0B5D3B] hover:bg-[#08472d] text-white text-xs font-bold rounded-xl shadow-md transition-all flex items-center gap-1.5 disabled:opacity-50"
                >
                  {busy === 'generate'
                    ? <Loader2 className="w-4 h-4 animate-spin" />
                    : <Sparkles className="w-4 h-4 text-[#D4A72C]" />}
                  <span>{busy === 'generate' ? 'Generating…' : 'Generate Fixtures'}</span>
                </button>
              ) : (
                <>
                  {/* Redrawing deletes every match and every board scored on
                      them, so it asks first rather than hinting in a tooltip. */}
                  <button
                    id="generate-fixtures-btn"
                    onClick={() => setIsRegenerateModalOpen(true)}
                    disabled={!!busy}
                    className="px-3.5 py-2 bg-emerald-50 hover:bg-emerald-100 text-[#0B5D3B] text-xs font-bold rounded-xl border border-emerald-200 transition-colors flex items-center gap-1.5 disabled:opacity-50"
                  >
                    {busy === 'generate'
                      ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      : <RefreshCw className="w-3.5 h-3.5" />}
                    <span>{busy === 'generate' ? 'Generating…' : 'Regenerate Fixtures'}</span>
                  </button>

                  {!tournament.scheduledPublished && (
                    <button
                      id="auto-schedule-btn"
                      onClick={runSchedule}
                      disabled={!!busy}
                      className="px-3.5 py-2 bg-amber-50 hover:bg-[#D4A72C]/10 text-amber-800 text-xs font-bold rounded-xl border border-amber-200 transition-colors flex items-center gap-1.5 disabled:opacity-50"
                      title="Generate conflict-free boards, timings, and rest periods"
                    >
                      {busy === 'schedule'
                        ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        : <Calendar className="w-3.5 h-3.5 text-[#D4A72C]" />}
                      <span>{busy === 'schedule' ? 'Scheduling…' : 'Auto-Schedule'}</span>
                    </button>
                  )}

                  {!tournament.scheduledPublished && (
                    <button
                      id="publish-schedule-btn"
                      onClick={() => setIsPublishModalOpen(true)}
                      disabled={!!busy}
                      className="px-4 py-2 bg-[#D4A72C] hover:bg-[#c29623] text-[#202522] text-xs font-bold rounded-xl shadow-md transition-all flex items-center gap-1.5 disabled:opacity-50"
                    >
                      {busy === 'publish'
                        ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        : <Send className="w-3.5 h-3.5 text-[#202522]" />}
                      <span>{busy === 'publish' ? 'Publishing…' : 'Publish Schedule'}</span>
                    </button>
                  )}

                  {/* A published schedule can still change: a board breaks, a
                      session runs late. Auto-Schedule and Publish disappear
                      once it is out, so this is the one control left for it. */}
                  {tournament.scheduledPublished && (
                    <button
                      id="reschedule-btn"
                      onClick={() => setIsRescheduleModalOpen(true)}
                      disabled={!!busy}
                      className="px-3.5 py-2 bg-amber-50 hover:bg-[#D4A72C]/10 text-amber-800 text-xs font-bold rounded-xl border border-amber-200 transition-colors flex items-center gap-1.5 disabled:opacity-50"
                    >
                      {busy === 'reschedule'
                        ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        : <Calendar className="w-3.5 h-3.5 text-[#D4A72C]" />}
                      <span>{busy === 'reschedule' ? 'Rescheduling…' : 'Reschedule'}</span>
                    </button>
                  )}

                  {/* Regenerating the draw discards every board already scored,
                      so a late entrant needs one fixture added, not a redraw. */}
                  <button
                    id="add-match-btn"
                    onClick={() => setIsAddMatchOpen(true)}
                    className="px-4 py-2 bg-white hover:bg-gray-50 text-[#0B5D3B] border border-[#0B5D3B] text-xs font-bold rounded-xl transition-all flex items-center gap-1.5"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    <span>Add Match</span>
                  </button>
                </>
              )}
            </>
          )}

          {/* View Mode Toggle */}
          <div className="flex items-center bg-gray-100 p-1 rounded-xl border border-gray-200">
            <button
              onClick={() => setViewMode('rounds')}
              className={`p-1.5 rounded-lg text-xs font-semibold flex items-center gap-1 transition-all ${
                viewMode === 'rounds' ? 'bg-white text-gray-900 shadow-xs' : 'text-gray-500 hover:text-gray-900'
              }`}
              title="Round by Round View"
            >
              <List className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Rounds</span>
            </button>
            <button
              onClick={() => setViewMode('boards')}
              className={`p-1.5 rounded-lg text-xs font-semibold flex items-center gap-1 transition-all ${
                viewMode === 'boards' ? 'bg-white text-gray-900 shadow-xs' : 'text-gray-500 hover:text-gray-900'
              }`}
              title="Board Timeline View"
            >
              <Grid className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Board Grid</span>
            </button>
          </div>
        </div>
      </div>

      {/* Directly under the buttons that caused it, not at the foot of a page
          of fixtures where it used to be. */}
      {actionError && (
        <div role="alert" className="p-3 rounded-xl bg-red-50 border border-red-200 text-xs text-red-800 flex items-start gap-1.5">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0 text-red-600" />
          <span className="flex-1 leading-snug">{actionError}</span>
        </div>
      )}

      {/* Who plays whom, and when. Rendered above the view toggle, because it
          used to live inside the rounds branch and vanished on Board Grid. */}
      {hasMatches && (
            <div className="w-full mb-2.5">
              <div className="relative">
                <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
                <input
                  type="text"
                  value={playerSearch}
                  onChange={e => setPlayerSearch(e.target.value)}
                  placeholder="Find a player or team — type a name to see their matches"
                  className="w-full text-sm pl-9 pr-9 py-2.5 border border-gray-200 rounded-xl bg-white focus:border-[#0B5D3B] focus:outline-hidden"
                />
                {playerSearch && (
                  <button
                    onClick={() => setPlayerSearch('')}
                    aria-label="Clear search"
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 p-1 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100"
                  >
                    <X className="w-4 h-4" />
                  </button>
                )}
              </div>
  
              {needle && (
                <div className="mt-2 px-3 py-2 rounded-xl bg-[#F8F6F0] border border-gray-200 text-xs">
                  {searchedMatches.length === 0 ? (
                    <span className="text-gray-600">
                      Nobody matching <span className="font-bold">“{playerSearch}”</span> has a fixture.
                    </span>
                  ) : (
                    <span className="text-gray-700">
                      <span className="font-bold">{searchedMatches.length}</span>
                      {' '}match{searchedMatches.length === 1 ? '' : 'es'} for{' '}
                      <span className="font-bold">“{playerSearch}”</span>
                      {(() => {
                        const next = searchedMatches
                          .filter(m => !m.resultConfirmed)
                          .sort((a, b) =>
                            compareMatches(a, b))[0];
                        if (!next) return <span className="text-gray-500"> · all played</span>;
                        const them = (next.player1Name || '').toLowerCase().includes(needle)
                          ? next.player2Name : next.player1Name;
                        return (
                          <span className="text-gray-600">
                            {' '}· next: <span className="font-bold text-[#0B5D3B]">vs {them}</span>
                            {next.scheduledDate || next.scheduledTime
                              ? <> on {next.scheduledDate} at {next.scheduledTime}</>
                              : <> (not scheduled yet)</>}
                            {' '}· Board {next.boardNumber}
                          </span>
                        );
                      })()}
                      <span className="text-gray-400"> · round filter ignored while searching</span>
                    </span>
                  )}
                </div>
              )}
            </div>
      )}

      {/* The state of the schedule, checked rather than asserted.
          This used to read "All Scheduling Constraints Verified" in green
          whenever any fixture existed -- nothing here ever called the conflict
          checker, so the one place an organiser looks for that assurance was
          the one place giving it without looking. */}
      {hasMatches && (() => {
        const scheduled = allMatches.filter(m => m.scheduledDate || m.scheduledTime).length;
        const unscheduled = allMatches.length - scheduled;
        const ok = unscheduled === 0;
        return (
          <div className={`rounded-xl p-3.5 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs border ${
            ok ? 'bg-emerald-50/70 border-emerald-200/80' : 'bg-amber-50 border-amber-200'
          }`}>
            <div className={`flex items-center space-x-2 ${ok ? 'text-emerald-950' : 'text-amber-950'}`}>
              {ok
                ? <ShieldCheck className="w-4 h-4 text-emerald-600 shrink-0" />
                : <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0" />}
              <span className="font-semibold">
                {ok ? 'Every match has a time and a board' : 'Not scheduled yet'}
              </span>
              <span className={ok ? 'text-emerald-800' : 'text-amber-800'}>
                {ok
                  ? 'Run Auto-Schedule again after any change to the draw.'
                  : `${unscheduled} of ${allMatches.length} matches have no date or time. Run Auto-Schedule to assign boards and times.`}
              </span>
            </div>

            <div className={`font-bold shrink-0 ${ok ? 'text-emerald-900' : 'text-amber-900'}`}>
              {matches.length} Matches · {tournament.numberOfBoards} Boards
            </div>
          </div>
        );
      })()}

      {/* Main Fixtures & Schedule Display */}
      {!hasMatches ? (
        <div className="bg-white rounded-2xl border border-gray-200/80 p-12 text-center shadow-xs">
          <div className="w-12 h-12 rounded-full bg-emerald-100 text-[#0B5D3B] flex items-center justify-center mx-auto mb-3">
            <Sparkles className="w-6 h-6 text-[#D4A72C]" />
          </div>
          <h4 className="text-base font-bold text-gray-900 mb-1">
            Fixtures Have Not Been Generated Yet
          </h4>
          <p className="text-xs text-gray-500 max-w-md mx-auto mb-4">
            Click "Generate Fixtures" to automatically compute all round-robin pairings or seeded knockout brackets according to tournament rules.
          </p>
          {role === 'admin' && (
            <button
              onClick={runGenerate}
              disabled={!!busy}
              className="px-5 py-2.5 bg-[#0B5D3B] hover:bg-[#08472d] text-white text-xs font-bold rounded-xl shadow-md inline-flex items-center gap-2 disabled:opacity-50"
            >
              {busy === 'generate'
                ? <Loader2 className="w-4 h-4 animate-spin" />
                : <Sparkles className="w-4 h-4 text-[#D4A72C]" />}
              <span>{busy === 'generate' ? 'Generating…' : 'Generate Automatic Fixtures'}</span>
            </button>
          )}
        </div>
      ) : viewMode === 'rounds' ? (
        
        /* Round by Round View */
        <div className="space-y-4">
          

          {/* Round Filter Tabs */}
          {rounds.length > 1 && (
            <div className="flex items-center gap-1.5 overflow-x-auto pb-1">

              {showCategoryTabs && (
                <div className="w-full flex items-center gap-1.5 mb-2 pb-2 border-b border-gray-200/70">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-gray-500 mr-1">
                    Category
                  </span>
                  {(['all', ...categoriesPresent] as const).map(cat => {
                    const count = cat === 'all'
                      ? allMatches.length
                      : allMatches.filter(m => m.type === cat).length;
                    const active = categoryFilter === cat;
                    return (
                      <button
                        key={cat}
                        onClick={() => { setCategoryFilter(cat as any); setSelectedRoundFilter('all'); }}
                        className={`px-3 py-1 rounded-lg text-[11px] font-bold capitalize transition-colors border ${
                          active
                            ? cat === 'doubles'
                              ? 'bg-blue-600 text-white border-blue-600'
                              : 'bg-[#0B5D3B] text-white border-[#0B5D3B]'
                            : 'bg-white text-gray-600 border-gray-200 hover:border-gray-300'
                        }`}
                      >
                        {cat === 'all' ? 'All' : cat} ({count})
                      </button>
                    );
                  })}
                </div>
              )}

              <button
                onClick={() => setSelectedRoundFilter('all')}
                className={`px-3 py-1.5 rounded-xl text-xs font-semibold transition-colors shrink-0 ${
                  selectedRoundFilter === 'all'
                    ? 'bg-[#0B5D3B] text-white'
                    : 'bg-white text-gray-700 hover:bg-gray-100 border border-gray-200'
                }`}
              >
                All Rounds ({matches.length})
              </button>
              {rounds.map(roundName => {
                const count = roundCounts.get(roundName) || 0;
                return (
                  <button
                    key={roundName}
                    onClick={() => setSelectedRoundFilter(roundName)}
                    className={`px-3 py-1.5 rounded-xl text-xs font-semibold transition-colors shrink-0 ${
                      selectedRoundFilter === roundName
                        ? 'bg-[#0B5D3B] text-white'
                        : 'bg-white text-gray-700 hover:bg-gray-100 border border-gray-200'
                    }`}
                  >
                    {roundName} ({count})
                  </button>
                );
              })}
            </div>
          )}

          {/* Matches Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3.5">
            {displayedMatches.map((match) => {
              const isLive = match.status === 'live';
              const isCompleted = match.status === 'completed';

              return (
                <div
                  key={match.id}
                  onClick={() => onOpenMatch(match)}
                  className={`bg-white rounded-2xl p-4 border transition-all cursor-pointer relative hover:shadow-md ${
                    isLive
                      ? 'border-orange-400 ring-2 ring-orange-400/20 bg-orange-50/20'
                      : isCompleted
                      ? 'border-gray-200/80 bg-gray-50/40 opacity-95'
                      : 'border-gray-200/80 hover:border-emerald-500'
                  }`}
                >
                  {/* Match Header Info */}
                  <div className="flex items-center justify-between text-xs pb-2.5 mb-2.5 border-b border-gray-100">
                    <div className="flex items-center space-x-1.5 font-bold text-gray-800">
                      <span className="text-[#0B5D3B]">Match #{match.matchNumber}</span>
                      <span className="text-gray-300">·</span>
                      <span className="text-gray-500 font-medium">{match.roundName}</span>
                    </div>

                    <div className="flex items-center space-x-1.5">
                      <span className="px-2 py-0.5 rounded-md bg-emerald-100 text-emerald-900 font-bold text-[10px]">
                        Board {match.boardNumber}
                      </span>
                      <span className={`px-2 py-0.5 rounded-md text-[10px] font-bold uppercase ${
                        isLive
                          ? 'bg-orange-500 text-white animate-pulse'
                          : isCompleted
                          ? 'bg-gray-200 text-gray-700'
                          : 'bg-blue-100 text-blue-800'
                      }`}>
                        {isLive ? 'LIVE' : match.status}
                      </span>
                    </div>
                  </div>

                  {/* Players & Board Scoreline */}
                  <div className="space-y-2 mb-3">
                    
                    {/* Player 1 */}
                    <div className={`flex items-center justify-between p-2 rounded-xl text-xs transition-colors ${
                      match.winnerId === match.player1Id
                        ? 'bg-emerald-50 font-bold text-emerald-950 border border-emerald-200/60'
                        : 'bg-gray-50/80 text-gray-800'
                    }`}>
                      <div className="flex items-center space-x-2 truncate">
                        <div className="w-5 h-5 rounded-full bg-white border border-gray-300 flex items-center justify-center font-bold text-[10px] text-gray-700 shrink-0">
                          1
                        </div>
                        <span className="truncate">{match.player1Name}</span>
                      </div>
                      <div className="flex items-center space-x-2 shrink-0">
                        <span className="font-bold text-sm text-gray-900">
                          {match.player1TotalPoints}
                        </span>
                        {match.winnerId === match.player1Id && (
                          <span className="text-[10px] px-1.5 py-0.2 rounded bg-emerald-600 text-white font-bold">
                            WIN ({match.player1BoardWins})
                          </span>
                        )}
                      </div>
                    </div>

                    {/* Player 2 */}
                    <div className={`flex items-center justify-between p-2 rounded-xl text-xs transition-colors ${
                      match.winnerId === match.player2Id
                        ? 'bg-emerald-50 font-bold text-emerald-950 border border-emerald-200/60'
                        : 'bg-gray-50/80 text-gray-800'
                    }`}>
                      <div className="flex items-center space-x-2 truncate">
                        <div className="w-5 h-5 rounded-full bg-gray-900 border border-gray-900 flex items-center justify-center font-bold text-[10px] text-white shrink-0">
                          2
                        </div>
                        <span className="truncate">{match.player2Name}</span>
                      </div>
                      <div className="flex items-center space-x-2 shrink-0">
                        <span className="font-bold text-sm text-gray-900">
                          {match.player2TotalPoints}
                        </span>
                        {match.winnerId === match.player2Id && (
                          <span className="text-[10px] px-1.5 py-0.2 rounded bg-emerald-600 text-white font-bold">
                            WIN ({match.player2BoardWins})
                          </span>
                        )}
                      </div>
                    </div>

                  </div>

                  {/* Match Footer: Timing & CTA */}
                  <div className="flex items-center justify-between text-[11px] text-gray-500 pt-2 border-t border-gray-100">
                    <div className="flex items-center space-x-1">
                      <Clock className="w-3.5 h-3.5 text-gray-400" />
                      {/* Once a match is under way the elapsed time is the useful
                          number, not the time it was scheduled for. */}
                      {match.isTimerRunning || match.timerElapsedSeconds > 0 ? (
                        <span className="font-bold text-gray-700 tabular-nums">
                          <MatchTimer match={match} /> elapsed
                        </span>
                      ) : (
                        <span>{match.scheduledDate || 'Today'} · {match.scheduledTime || '09:00 AM'}</span>
                      )}
                    </div>

                    <span className="text-[#0B5D3B] font-bold flex items-center gap-0.5 group-hover:translate-x-0.5 transition-transform">
                      <span>{role !== 'admin' ? 'View Scores' : match.status === 'scheduled' ? 'Start Match' : 'Control Match'}</span>
                      <ArrowRight className="w-3 h-3" />
                    </span>
                  </div>

                </div>
              );
            })}
          </div>
        </div>
      ) : (

        /* Board Timeline View */
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {Array.from(boardMap.entries()).map(([boardNum, boardMatches]) => (
            <div 
              key={boardNum}
              className="bg-white rounded-2xl border border-gray-200/80 overflow-hidden shadow-xs flex flex-col"
            >
              {/* Board Header */}
              <div className="bg-[#0B5D3B] text-white px-4 py-3 flex items-center justify-between">
                <div className="font-serif font-bold text-sm flex items-center gap-1.5">
                  <div className="w-2.5 h-2.5 rounded-full bg-[#D4A72C]" />
                  <span>Board #{boardNum}</span>
                </div>
                <span className="text-[10px] bg-emerald-900 text-emerald-200 px-2 py-0.5 rounded-md font-semibold">
                  {boardMatches.length} Matches
                </span>
              </div>

              {/* Timeline slots */}
              <div className="p-3 space-y-2.5 flex-1 overflow-y-auto max-h-[500px] bg-gray-50/50 divide-y divide-gray-100">
                {boardMatches.length === 0 ? (
                  <div className="py-8 text-center text-gray-400 text-xs">
                    No matches assigned
                  </div>
                ) : (
                  boardMatches.map(m => (
                    <div
                      key={m.id}
                      onClick={() => onOpenMatch(m)}
                      className={`pt-2.5 first:pt-0 cursor-pointer group`}
                    >
                      <div className="flex items-center justify-between text-[10px] text-gray-500 mb-1">
                        <span className="font-bold text-gray-700">{m.scheduledTime}</span>
                        <span className={`px-1.5 py-0.2 rounded font-bold capitalize ${
                          m.status === 'live' ? 'bg-orange-500 text-white' :
                          m.status === 'completed' ? 'bg-gray-200 text-gray-700' :
                          'bg-blue-100 text-blue-800'
                        }`}>
                          {m.status}
                        </span>
                      </div>

                      <div className="bg-white p-2.5 rounded-xl border border-gray-200 group-hover:border-emerald-500 transition-colors shadow-2xs">
                        <div className="text-xs font-bold text-gray-900 truncate">
                          {m.player1Name}
                        </div>
                        <div className="text-[10px] text-gray-400 font-medium">vs</div>
                        <div className="text-xs font-bold text-gray-900 truncate">
                          {m.player2Name}
                        </div>

                        {m.resultConfirmed && (
                          <div className="mt-1 text-[10px] text-emerald-700 font-bold">
                            {m.walkover
                              ? <>Walkover to {m.winnerName}{m.walkoverReason ? ` — ${m.walkoverReason}` : ''}</>
                              : <>Final: {m.player1BoardWins} - {m.player2BoardWins} (Winner: {m.winnerName})</>}
                          </div>
                        )}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {isAddMatchOpen && (
        <AddMatchModal
          tournament={tournament}
          onClose={() => setIsAddMatchOpen(false)}
          onAdded={() => setIsAddMatchOpen(false)}
        />
      )}

      {/* Confirmation Modal to Publish Schedule */}
      <ConfirmationModal
        isOpen={isPublishModalOpen}
        onClose={() => setIsPublishModalOpen(false)}
        onConfirm={runPublish}
        title="Publish Official Schedule?"
        description="Publishing the schedule makes all match timings and board allocations visible to all players and enables live match scoring."
        confirmLabel="Publish Schedule to Players"
        variant="primary"
      />

      <ConfirmationModal
        isOpen={isRegenerateModalOpen}
        onClose={() => setIsRegenerateModalOpen(false)}
        onConfirm={runRegenerate}
        title="Regenerate the draw?"
        description={
          `This deletes all ${allMatches.length} matches and every board scored on them, along with their correction history, and draws the tournament again from the approved entries. `
          + (playedBoards > 0 || confirmedMatches > 0
              ? `${playedBoards} board${playedBoards === 1 ? '' : 's'} with play recorded and ${confirmedMatches} confirmed result${confirmedMatches === 1 ? '' : 's'} will be discarded. There is no undo. `
              : 'No board has been played yet, so nothing scored is lost. ')
          + 'If someone joined late, use Add Match instead.'
        }
        confirmLabel={playedBoards > 0 || confirmedMatches > 0 ? 'Discard results and redraw' : 'Redraw fixtures'}
        variant="danger"
      />

      <ConfirmationModal
        isOpen={isRescheduleModalOpen}
        onClose={() => setIsRescheduleModalOpen(false)}
        onConfirm={runReschedule}
        title="Reschedule the published schedule?"
        description={`Every match — including any already played — is given a fresh board and time using the ${restMinutes}-minute rest buffer, and the new schedule is published straight away, so every participant is notified again. Scores and results are not touched.`}
        confirmLabel="Reschedule and Republish"
        variant="warning"
      />

    </div>
  );
};
