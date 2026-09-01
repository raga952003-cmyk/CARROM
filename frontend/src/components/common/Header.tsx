import React, { useState } from 'react';
import { Trophy, User, ShieldAlert, Bell, PlusCircle, RotateCcw, Sparkles, ChevronDown, CheckCircle2, Calendar, Layers, LogOut, Settings } from 'lucide-react';
import { Avatar } from './Avatar';
import { ProfileSettings } from './ProfileSettings';
import { useTournament } from '../../context/TournamentContext';

interface HeaderProps {
  onOpenCreateModal: () => void;
  onToggleNotifs: () => void;
  unreadCount: number;
}

export const Header: React.FC<HeaderProps> = ({ 
  onOpenCreateModal, 
  onToggleNotifs, 
  unreadCount 
}) => {
  const { 
    role, 
    currentUser,
    refreshCurrentUser,
    signOutUser,
    tournaments, 
    activeTournamentId, 
    setActiveTournamentId,
    resetToSampleData,
    realtimeStatus
  } = useTournament();

  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const [isPlayerMenuOpen, setIsPlayerMenuOpen] = useState(false);
  const [isTourMenuOpen, setIsTourMenuOpen] = useState(false);

  return (
    <>
    <header id="app-header" className="sticky top-0 z-40 bg-[#0B5D3B] text-white shadow-md border-b border-[#ffffff22]">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16 sm:h-20">
          
          {/* Logo & Brand Identity */}
          <div className="flex items-center space-x-3 min-w-0">
            <div className="w-9 h-9 sm:w-10 sm:h-10 rounded-full bg-[#D4A72C] flex items-center justify-center text-[#0B5D3B] font-black text-lg sm:text-xl shadow-md border-2 border-white/20 transform -rotate-3 hover:rotate-0 transition-transform">
              C
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <span className="font-bold text-lg sm:text-xl tracking-tight text-white flex items-center gap-1.5">
                  Carrom Pro
                </span>
                <span className="hidden md:inline-flex px-2.5 py-0.5 text-[10px] font-bold tracking-wider uppercase bg-[#D4A72C] text-[#0B5D3B] rounded-full shadow-xs">
                  AICF Standard
                </span>
                {/* Live-data channel indicator: 'live' means Supabase Realtime is
                    streaming; anything else means the slow fallback is in use. */}
                <span
                  title={
                    realtimeStatus === 'live'
                      ? 'Live updates streaming via Supabase Realtime'
                      : realtimeStatus === 'polling'
                        ? 'Realtime unavailable — falling back to periodic refresh'
                        : realtimeStatus === 'disabled'
                          ? 'Realtime not configured (VITE_SUPABASE_URL / ANON_KEY missing)'
                          : 'Connecting to the live update channel…'
                  }
                  className={`hidden lg:inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-bold tracking-wider uppercase rounded-full border ${
                    realtimeStatus === 'live'
                      ? 'bg-emerald-900/60 text-emerald-100 border-emerald-500/50'
                      : 'bg-amber-900/50 text-amber-100 border-amber-500/50'
                  }`}
                >
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${
                      realtimeStatus === 'live' ? 'bg-emerald-300 animate-pulse' : 'bg-amber-300'
                    }`}
                  />
                  {realtimeStatus === 'live' ? 'Live' : realtimeStatus}
                </span>
              </div>
              <p className="text-[11px] text-emerald-100/90 font-medium hidden sm:block tracking-wide">
                Automated Tournament Operations & Scoring Engine
              </p>
            </div>
          </div>

          {/* Center: Tournament Selector Jump */}
          <div className="hidden lg:flex items-center">
            <div className="relative">
              <button
                id="header-tournament-selector-btn"
                onClick={() => setIsTourMenuOpen(!isTourMenuOpen)}
                className="flex items-center space-x-2 bg-emerald-900/60 hover:bg-emerald-900/90 text-emerald-100 px-3 py-1.5 rounded-lg border border-emerald-700/50 text-xs font-medium transition-colors"
              >
                <Trophy className="w-3.5 h-3.5 text-[#D4A72C]" />
                <span className="max-w-[180px] truncate">
                  {tournaments.find(t => t.id === activeTournamentId)?.name || 'Select Tournament'}
                </span>
                <ChevronDown className="w-3 h-3 text-emerald-300" />
              </button>

              {isTourMenuOpen && (
                <div className="absolute top-full mt-2 w-72 bg-white text-[#202522] rounded-xl shadow-xl border border-gray-100 py-1.5 z-50 animate-in fade-in slide-in-from-top-2 duration-150">
                  <div className="px-3 py-1 text-[11px] font-semibold text-gray-600 uppercase tracking-wider">
                    All Tournaments
                  </div>
                  {tournaments.map(t => (
                    <button
                      key={t.id}
                      onClick={() => {
                        setActiveTournamentId(t.id);
                        setIsTourMenuOpen(false);
                      }}
                      className={`w-full text-left px-3 py-2 text-xs flex items-center justify-between hover:bg-emerald-50 transition-colors ${
                        t.id === activeTournamentId ? 'bg-emerald-50/80 font-semibold text-[#0B5D3B]' : 'text-gray-700'
                      }`}
                    >
                      <span className="truncate pr-2">{t.name}</span>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded capitalize ${
                        t.status === 'ongoing' ? 'bg-amber-100 text-amber-800' :
                        t.status === 'registration_open' ? 'bg-emerald-100 text-emerald-800' :
                        'bg-gray-100 text-gray-600'
                      }`}>
                        {t.status.replace('_', ' ')}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Right Section: Role Switcher, Reset, Notifs, Actions */}
          {/* shrink-0 + tighter gaps: on a 375px screen this cluster used to
              push the page wider than the viewport. */}
          <div className="flex items-center gap-1.5 sm:gap-3 shrink-0">
            
            {/* Logged in User Profile — now a way in to your own account */}
            <div className="relative shrink-0">
              <button
                id="profile-menu-btn"
                onClick={() => setProfileMenuOpen(o => !o)}
                aria-haspopup="menu"
                aria-expanded={profileMenuOpen}
                className="flex items-center gap-2 bg-emerald-950/70 hover:bg-emerald-900/80 py-1.5 px-1.5 sm:px-3 rounded-xl border border-emerald-800/80 shadow-inner transition-colors"
              >
                <Avatar name={currentUser?.name} src={(currentUser as any)?.avatar} size={22} />
                <div className="text-left leading-none hidden sm:block">
                  <div className="text-[11px] font-bold text-white truncate max-w-[100px]">
                    {currentUser?.name || 'User'}
                  </div>
                  <div className="text-[8px] text-[#D4A72C] font-semibold uppercase tracking-wider mt-0.5">
                    {role === 'admin' ? 'Federation Admin' : 'Competitor'}
                  </div>
                </div>
                <ChevronDown className="w-3 h-3 text-emerald-300 hidden sm:block" />
              </button>

              {profileMenuOpen && (
                <>
                  {/* Click anywhere else to dismiss, which is what people expect
                      of a menu and what a bare dropdown never does. */}
                  <div className="fixed inset-0 z-40" onClick={() => setProfileMenuOpen(false)} />
                  <div
                    role="menu"
                    className="absolute right-0 mt-2 w-56 bg-white rounded-xl shadow-2xl border border-gray-200 z-50 overflow-hidden"
                  >
                    <div className="px-4 py-3 bg-[#F8F6F0] border-b border-gray-200 flex items-center gap-2.5">
                      <Avatar name={currentUser?.name} src={(currentUser as any)?.avatar} size={36} />
                      <div className="min-w-0">
                        <div className="text-sm font-bold text-gray-900 truncate">
                          {currentUser?.name || 'User'}
                        </div>
                        <div className="text-[11px] text-gray-500 truncate">
                          {(currentUser as any)?.email}
                        </div>
                      </div>
                    </div>
                    <button
                      role="menuitem"
                      onClick={() => { setProfileMenuOpen(false); setSettingsOpen(true); }}
                      className="w-full px-4 py-2.5 text-left text-sm font-medium text-gray-800 hover:bg-gray-50 flex items-center gap-2.5"
                    >
                      <Settings className="w-4 h-4 text-gray-500" />
                      Settings
                    </button>
                  </div>
                </>
              )}
            </div>

            {/* Sign Out Button */}
            <button
              id="sign-out-btn"
              onClick={() => {
                if (confirm("Are you sure you want to sign out?")) {
                  signOutUser();
                }
              }}
              title="Sign out"
              aria-label="Sign out"
              className="flex items-center px-2 sm:px-2.5 py-1.5 bg-red-800/40 hover:bg-red-800/80 text-red-200 hover:text-white rounded-lg border border-red-900/40 text-[11px] font-semibold transition-all shrink-0"
            >
              <LogOut className="w-3.5 h-3.5 sm:hidden" />
              <span className="hidden sm:inline">Sign Out</span>
            </button>

            {/* Create Tournament CTA (Admin only) */}
            {role === 'admin' && (
              <button
                id="create-tournament-header-btn"
                onClick={onOpenCreateModal}
                className="flex items-center gap-1.5 bg-[#D4A72C] hover:bg-[#c29623] text-[#202522] px-2.5 sm:px-3 py-1.5 rounded-lg text-xs font-bold shadow-md hover:shadow transition-all transform active:scale-95 shrink-0"
              >
                <PlusCircle className="w-3.5 h-3.5 text-[#202522]" />
                <span className="hidden sm:inline">Create Tournament</span>
                <span className="sm:hidden">New</span>
              </button>
            )}

            {/* Notifications Button */}
            <button
              id="notifications-toggle-btn"
              onClick={onToggleNotifs}
              className="relative p-2 rounded-lg bg-emerald-900/60 hover:bg-emerald-900 text-emerald-200 hover:text-white transition-colors"
              title="Tournament Notifications"
            >
              <Bell className="w-4 h-4" />
              {unreadCount > 0 && (
                <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-amber-400 text-emerald-950 font-bold text-[9px] flex items-center justify-center shadow-xs">
                  {unreadCount > 9 ? '9+' : unreadCount}
                </span>
              )}
            </button>

            {/* Reset sample data */}
            <button
              id="reset-sample-data-btn"
              onClick={() => {
                if (confirm("Reset all tournaments and scores to standard sample state?")) {
                  resetToSampleData();
                }
              }}
              className="p-2 rounded-lg bg-emerald-900/40 hover:bg-emerald-900/80 text-emerald-300 hover:text-white transition-colors text-xs"
              title="Reset Sample Data"
            >
              <RotateCcw className="w-3.5 h-3.5" />
            </button>

          </div>
        </div>
      </div>
    </header>

      {settingsOpen && currentUser && (
        <ProfileSettings
          user={currentUser as any}
          onClose={() => setSettingsOpen(false)}
          onSaved={() => refreshCurrentUser()}
        />
      )}
    </>
  );
};
