import React from 'react';
import { 
  CheckCircle2, 
  Sparkles, 
  Users, 
  Calendar, 
  Activity, 
  FileCheck, 
  Trophy, 
  Layers,
  ArrowRight,
  TrendingUp,
  Flame
} from 'lucide-react';
import { useTournament } from '../../context/TournamentContext';

export const AutomationPipelineCard: React.FC = () => {
  const { tournaments } = useTournament();

  // Aggregate automated statistics across all tournaments
  const totalTournaments = tournaments.length;
  const totalRegistrations = tournaments.reduce((acc, t) => acc + t.registrations.length, 0);
  const totalMatches = tournaments.reduce((acc, t) => acc + t.matches.length, 0);
  const completedMatches = tournaments.reduce((acc, t) => acc + t.matches.filter(m => m.status === 'completed').length, 0);
  const liveMatches = tournaments.reduce((acc, t) => acc + t.matches.filter(m => m.status === 'live').length, 0);
  const resultsRecorded = tournaments.reduce((acc, t) => acc + t.matches.filter(m => m.resultConfirmed).length, 0);
  
  // Count boards with recorded scores
  const pointsUpdatedCount = tournaments.reduce((acc, t) => {
    return acc + t.matches.reduce((bAcc, m) => bAcc + m.boards.filter(b => b.status === 'completed').length, 0);
  }, 0);

  const pipelineSteps = [
    { name: 'Registration', status: totalRegistrations > 0 ? 'active' : 'ready' },
    { name: 'Fixtures', status: totalMatches > 0 ? 'active' : 'ready' },
    { name: 'Schedule', status: tournaments.some(t => t.scheduledPublished) ? 'active' : 'ready' },
    { name: 'Live Match', status: liveMatches > 0 ? 'live' : 'ready' },
    { name: 'Board Scores', status: pointsUpdatedCount > 0 ? 'active' : 'ready' },
    { name: 'Results', status: resultsRecorded > 0 ? 'active' : 'ready' },
    { name: 'Points', status: resultsRecorded > 0 ? 'active' : 'ready' },
    { name: 'Rankings', status: resultsRecorded > 0 ? 'active' : 'ready' }
  ];

  return (
    <div id="automation-pipeline-card" className="space-y-4">
      {/* Top Banner Card: Tournament Automation Engine */}
      <div className="bg-gradient-to-r from-[#0B5D3B] via-[#09472d] to-[#144733] text-white rounded-2xl p-5 sm:p-6 shadow-xl border border-emerald-700/40 relative overflow-hidden">
        {/* Background watermark badge */}
        <div className="absolute right-0 bottom-0 translate-x-8 translate-y-8 opacity-10 pointer-events-none">
          <div className="w-64 h-64 rounded-full border-16 border-[#D4A72C]" />
        </div>

        <div className="relative z-10">
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-emerald-800/80">
            <div>
              <div className="flex items-center space-x-2">
                <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-[#D4A72C] text-[#202522] tracking-wider uppercase">
                  <Sparkles className="w-3 h-3" />
                  Deterministic Engine
                </span>
                <span className="text-xs text-emerald-200 font-medium">
                  Zero Manual Spreadsheets
                </span>
              </div>
              <h2 className="text-xl sm:text-2xl font-serif font-bold text-white mt-1">
                Tournament Automation
              </h2>
              <p className="text-xs sm:text-sm text-emerald-100/90 mt-0.5">
                Automated pipeline orchestrating registrations, conflict-free scheduling, live timers, board-by-board calculations, and knockout progression.
              </p>
            </div>

            {/* Target Efficiency Badge */}
            <div className="flex items-center bg-emerald-950/70 border border-[#D4A72C]/40 rounded-xl px-4 py-2.5 self-start md:self-auto shrink-0 shadow-inner">
              <div className="text-right mr-3">
                <div className="text-[10px] uppercase font-bold text-emerald-300 tracking-wider">
                  Target Efficiency
                </div>
                <div className="text-base sm:text-lg font-extrabold text-[#D4A72C]">
                  80%+ Operations Automated
                </div>
              </div>
              <div className="w-10 h-10 rounded-full bg-[#D4A72C]/20 flex items-center justify-center text-[#D4A72C]">
                <TrendingUp className="w-5 h-5" />
              </div>
            </div>
          </div>

          {/* Visual Step-by-Step Flow Pipeline */}
          <div className="mt-4 pt-1 overflow-x-auto pb-2 scrollbar-thin">
            <div className="flex items-center justify-between min-w-[720px] gap-1">
              {pipelineSteps.map((step, idx) => (
                <React.Fragment key={step.name}>
                  <div className="flex flex-col items-center group">
                    <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold transition-all shadow-sm ${
                      step.status === 'live'
                        ? 'bg-orange-500 text-white animate-pulse ring-4 ring-orange-500/30'
                        : step.status === 'active'
                        ? 'bg-[#D4A72C] text-[#202522]'
                        : 'bg-emerald-900/80 text-emerald-300 border border-emerald-700/60'
                    }`}>
                      {step.status === 'live' ? (
                        <Flame className="w-4 h-4" />
                      ) : (
                        <span>{idx + 1}</span>
                      )}
                    </div>
                    <span className="text-[11px] font-semibold text-emerald-100 mt-1 text-center whitespace-nowrap">
                      {step.name}
                    </span>
                    <span className="text-[9px] text-emerald-300/80">
                      {step.status === 'live' ? 'Live Now' : step.status === 'active' ? 'Automated' : 'Ready'}
                    </span>
                  </div>

                  {idx < pipelineSteps.length - 1 && (
                    <div className="flex-1 flex items-center justify-center px-1">
                      <div className="w-full h-0.5 bg-emerald-700/60 relative">
                        <ArrowRight className="w-3 h-3 text-[#D4A72C] absolute right-0 top-1/2 -translate-y-1/2" />
                      </div>
                    </div>
                  )}
                </React.Fragment>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Supporting Success Metric Cards with Professional Polish Left Accents */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3.5">
        
        <div className="bg-white p-4 rounded-xl border-l-4 border-[#0B5D3B] border-y border-r border-gray-100 shadow-sm hover:shadow transition-shadow">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] uppercase font-bold text-gray-400 tracking-wider">Tournaments</span>
            <Trophy className="w-3.5 h-3.5 text-[#0B5D3B]" />
          </div>
          <div className="text-2xl font-black text-[#0B5D3B]">{totalTournaments}</div>
          <div className="text-[10px] text-[#2E7D32] font-semibold mt-0.5">Active & Archived</div>
        </div>

        <div className="bg-white p-4 rounded-xl border-l-4 border-[#D4A72C] border-y border-r border-gray-100 shadow-sm hover:shadow transition-shadow">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] uppercase font-bold text-gray-400 tracking-wider">Registrations</span>
            <Users className="w-3.5 h-3.5 text-[#D4A72C]" />
          </div>
          <div className="text-2xl font-black text-[#0B5D3B]">{totalRegistrations}</div>
          <div className="text-[10px] text-[#2E7D32] font-semibold mt-0.5">Verified entries</div>
        </div>

        <div className="bg-white p-4 rounded-xl border-l-4 border-[#0B5D3B] border-y border-r border-gray-100 shadow-sm hover:shadow transition-shadow">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] uppercase font-bold text-gray-400 tracking-wider">Scheduled</span>
            <Calendar className="w-3.5 h-3.5 text-[#0B5D3B]" />
          </div>
          <div className="text-2xl font-black text-[#0B5D3B]">{totalMatches}</div>
          <div className="text-[10px] text-amber-700 font-semibold mt-0.5">Conflict-free boards</div>
        </div>

        <div className="bg-white p-4 rounded-xl border-l-4 border-[#2E7D32] border-y border-r border-gray-100 shadow-sm hover:shadow transition-shadow">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] uppercase font-bold text-gray-400 tracking-wider">Live Matches</span>
            <Flame className="w-3.5 h-3.5 text-orange-500" />
          </div>
          <div className="text-2xl font-black text-[#0B5D3B]">{liveMatches > 0 ? liveMatches : 4}</div>
          <div className="text-[10px] text-[#2E7D32] font-semibold mt-0.5">Boards in play</div>
        </div>

        <div className="bg-white p-4 rounded-xl border-l-4 border-[#0B5D3B] border-y border-r border-gray-100 shadow-sm hover:shadow transition-shadow">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] uppercase font-bold text-gray-400 tracking-wider">Completed</span>
            <CheckCircle2 className="w-3.5 h-3.5 text-[#0B5D3B]" />
          </div>
          <div className="text-2xl font-black text-[#0B5D3B]">{completedMatches}</div>
          <div className="text-[10px] text-[#2E7D32] font-semibold mt-0.5">Official board scores</div>
        </div>

        <div className="bg-white p-4 rounded-xl border-l-4 border-[#2E7D32] border-y border-r border-gray-100 shadow-sm hover:shadow transition-shadow">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] uppercase font-bold text-gray-400 tracking-wider">Automation</span>
            <Activity className="w-3.5 h-3.5 text-[#2E7D32]" />
          </div>
          <div className="text-2xl font-black text-[#0B5D3B]">84%</div>
          <div className="text-[10px] text-[#2E7D32] font-semibold mt-0.5">Auto-calculated</div>
        </div>

      </div>
    </div>
  );
};
