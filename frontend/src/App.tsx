import React, { useState } from 'react';
import { TournamentProvider, useTournament } from './context/TournamentContext';
import { useHashRoute } from './utils/useHashRoute';
import { BoardMode } from './components/scorer/BoardMode';
import { SpectatorView } from './components/public/SpectatorView';
import { PrintSheets } from './components/print/PrintSheets';
import { Header } from './components/common/Header';
import { NotificationDrawer } from './components/common/NotificationDrawer';
import { AdminDashboard } from './components/admin/AdminDashboard';
import { PlayerDashboard } from './components/player/PlayerDashboard';
import { CreateTournamentModal } from './components/admin/CreateTournamentModal';
import { AuthPortal } from './components/common/AuthPortal';
import { ShieldCheck } from 'lucide-react';

const TournamentApp: React.FC = () => {
  const [isNotificationOpen, setIsNotificationOpen] = useState(false);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);

  const { role, notifications, currentUser } = useTournament();
  const route = useHashRoute();
  const unreadCount = notifications.filter(n => !n.read).length;

  // Board mode and the print sheets are signed-in surfaces, but they replace
  // the dashboard chrome entirely -- a scorer at a board and a sheet headed
  // for a printer both want the screen to themselves.
  if (currentUser && route.view === 'board') {
    return (
      <BoardMode
        boardNumber={Number(route.segments[1]) || 1}
        tournamentId={route.params.get('t') || undefined}
      />
    );
  }
  if (currentUser && route.view === 'print') {
    return (
      <PrintSheets
        kind={(route.segments[1] as any) || 'all'}
        tournamentId={route.segments[2] || ''}
      />
    );
  }

  if (!currentUser) {
    return <AuthPortal />;
  }

  return (
    <div className="min-h-screen bg-[#F8F6F0] flex flex-col font-sans text-[#202522]">
      {/* Navigation Header */}
      <Header 
        onOpenCreateModal={() => setIsCreateModalOpen(true)} 
        onToggleNotifs={() => setIsNotificationOpen(!isNotificationOpen)} 
        unreadCount={unreadCount} 
      />

      {/* Main Workspace Container */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-3 sm:px-6 lg:px-8 py-4 sm:py-6">
        {role === 'admin' ? (
          <AdminDashboard />
        ) : (
          <PlayerDashboard />
        )}
      </main>

      {/* Create Tournament Modal */}
      <CreateTournamentModal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
      />

      {/* Activity Notifications Feed Drawer */}
      <NotificationDrawer
        isOpen={isNotificationOpen}
        onClose={() => setIsNotificationOpen(false)}
      />

      {/* Professional Polish Refined Footer */}
      <footer className="mt-auto border-t border-gray-200/80 bg-white py-3.5 px-4 sm:px-8">
        <div className="max-w-7xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-3 text-[11px] font-medium text-gray-500">
          <div className="flex items-center space-x-2">
            <div className="w-5 h-5 rounded-full bg-[#0B5D3B] flex items-center justify-center text-[#D4A72C] font-bold text-[10px]">
              C
            </div>
            <span className="font-bold text-gray-800 uppercase tracking-wider text-[10px]">Carrom Tournament Pro</span>
            <span>·</span>
            <span className="text-gray-600">All-India Carrom Federation (AICF) Standards</span>
          </div>

          <div className="flex items-center space-x-4 text-[10px] font-bold uppercase tracking-wider">
            <span className="flex items-center gap-1 text-[#2E7D32]">
              <ShieldCheck className="w-3.5 h-3.5" />
              <span>Conflict-Free Engine Active</span>
            </span>
            <span>·</span>
            <span className="flex items-center gap-1 text-gray-400">
              <span>84% Operations Automated</span>
            </span>
          </div>
        </div>
      </footer>
    </div>
  );
};

// Wrapper component that safely renders AuthPortal with context
const AppWrapper: React.FC = () => {
  const route = useHashRoute();

  // Public board: rendered outside the provider so it never needs a session
  // and never triggers the authenticated refresh loop.
  if (route.view === 'live') {
    return <SpectatorView tournamentId={route.segments[1] || undefined} />;
  }

  return (
    <TournamentProvider>
      <TournamentApp />
    </TournamentProvider>
  );
};

export default AppWrapper;
