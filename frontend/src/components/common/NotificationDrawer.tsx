import React from 'react';
import { 
  X, 
  Check, 
  Bell, 
  Calendar, 
  Trophy, 
  Flame, 
  Clock, 
  CheckCheck 
} from 'lucide-react';
import { useTournament } from '../../context/TournamentContext';

interface NotificationDrawerProps {
  isOpen: boolean;
  onClose: () => void;
}

export const NotificationDrawer: React.FC<NotificationDrawerProps> = ({ isOpen, onClose }) => {
  const { notifications, markNotificationAsRead, markAllNotificationsAsRead } = useTournament();

  if (!isOpen) return null;

  const getIcon = (type: string) => {
    switch (type) {
      case 'match_approaching':
        return <Flame className="w-4 h-4 text-orange-500" />;
      case 'result_confirmed':
        return <Trophy className="w-4 h-4 text-[#D4A72C]" />;
      case 'schedule_published':
        return <Calendar className="w-4 h-4 text-emerald-600" />;
      case 'registration_confirmed':
        return <Check className="w-4 h-4 text-emerald-600" />;
      default:
        return <Bell className="w-4 h-4 text-blue-500" />;
    }
  };

  const formatTime = (isoString: string) => {
    try {
      const d = new Date(isoString);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) + ' · ' + d.toLocaleDateString([], { month: 'short', day: 'numeric' });
    } catch {
      return isoString;
    }
  };

  return (
    <div className="fixed inset-0 z-50 overflow-hidden bg-black/40 backdrop-blur-xs flex justify-end animate-in fade-in duration-150">
      <div 
        id="notification-drawer"
        className="w-full max-w-md bg-white shadow-2xl h-full flex flex-col border-l border-gray-200 transform transition-transform animate-in slide-in-from-right duration-200"
      >
        {/* Drawer Header */}
        <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between bg-[#0B5D3B] text-white">
          <div className="flex items-center space-x-2">
            <Bell className="w-5 h-5 text-[#D4A72C]" />
            <h2 className="font-semibold text-base">Tournament Activity Feed</h2>
          </div>
          <div className="flex items-center space-x-2">
            <button
              onClick={markAllNotificationsAsRead}
              className="text-xs text-emerald-200 hover:text-white flex items-center gap-1 px-2 py-1 rounded hover:bg-emerald-900/60 transition-colors"
              title="Mark all as read"
            >
              <CheckCheck className="w-3.5 h-3.5" />
              <span>Mark all read</span>
            </button>
            <button
              onClick={onClose}
              className="p-1 rounded-lg text-emerald-200 hover:text-white hover:bg-emerald-900 transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* List of Notifications */}
        <div className="flex-1 overflow-y-auto divide-y divide-gray-100 p-2">
          {notifications.length === 0 ? (
            <div className="py-16 text-center text-gray-600">
              <Bell className="w-10 h-10 mx-auto text-gray-300 mb-2" />
              <p className="text-sm font-medium">No tournament notifications yet</p>
              <p className="text-xs text-gray-600 mt-1">Updates on matches, scores, and schedules will appear here.</p>
            </div>
          ) : (
            notifications.map(n => (
              <div
                key={n.id}
                onClick={() => markNotificationAsRead(n.id)}
                className={`p-3.5 rounded-xl transition-all cursor-pointer mb-1.5 flex gap-3 ${
                  n.read ? 'bg-white hover:bg-gray-50 opacity-80' : 'bg-emerald-50/70 border border-emerald-200/60 shadow-xs'
                }`}
              >
                <div className="mt-0.5 p-2 rounded-lg bg-white shadow-xs border border-gray-100 shrink-0">
                  {getIcon(n.type)}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between mb-1">
                    <h4 className="text-xs font-bold text-gray-900 truncate">
                      {n.title}
                    </h4>
                    {!n.read && (
                      <span className="w-2 h-2 rounded-full bg-emerald-600 shrink-0 ml-2" />
                    )}
                  </div>
                  <p className="text-xs text-gray-600 leading-relaxed mb-1.5">
                    {n.message}
                  </p>
                  <div className="flex items-center text-[10px] text-gray-600 gap-1">
                    <Clock className="w-3 h-3" />
                    <span>{formatTime(n.timestamp)}</span>
                    {n.tournamentName && (
                      <span className="font-medium text-emerald-800 ml-1 truncate">
                        · {n.tournamentName}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
};
