import React, { useMemo } from 'react';
import { findMyMatches } from '../../utils/myMatches';
import { Clock, MapPin, ChevronRight, CheckCircle2, Radio } from 'lucide-react';
import { Tournament, Match, Player, Admin } from '../../types/tournament';

interface NextMatchCardProps {
  tournament?: Tournament;
  currentUser: Player | Admin | null;
  onOpenMatch?: (match: Match) => void;
}

/**
 * "Where am I playing next?" — the one thing a competitor needs at a venue.
 *
 * Someone entered in both singles and doubles appears in a couple of hundred
 * fixtures; scanning that list for their own next match is the wrong job to
 * give them.
 */
export const NextMatchCard: React.FC<NextMatchCardProps> = ({
  tournament, currentUser, onOpenMatch,
}) => {
  const mine = useMemo(
    () => findMyMatches(tournament, currentUser),
    [tournament, currentUser]
  );

  const live = mine.find(m => m.status === 'live');
  const next = live || mine.find(m => !m.resultConfirmed);
  const played = mine.filter(m => m.resultConfirmed).length;

  if (!tournament || mine.length === 0) return null;

  if (!next) {
    return (
      <div className="rounded-2xl border border-emerald-200 bg-emerald-50/70 p-4 flex items-center gap-3">
        <CheckCircle2 className="w-6 h-6 text-emerald-600 shrink-0" />
        <div>
          <div className="text-sm font-bold text-emerald-900">All your matches are done</div>
          <div className="text-[11px] text-emerald-800">
            {played} played in {tournament.name}
          </div>
        </div>
      </div>
    );
  }

  const isMe = (id?: string, name?: string) =>
    id === currentUser?.id || name === currentUser?.name;
  const opponent = isMe(next.player1Id, next.player1Name) ? next.player2Name : next.player1Name;

  return (
    <button
      type="button"
      onClick={() => onOpenMatch?.(next)}
      className={`w-full text-left rounded-2xl p-4 border-2 transition-colors ${
        live
          ? 'bg-red-50 border-red-300'
          : 'bg-[#0B5D3B] border-[#0B5D3B] text-white'
      }`}
    >
      <div className={`flex items-center gap-1.5 text-[10px] font-black uppercase tracking-widest ${
        live ? 'text-red-700' : 'text-[#D4A72C]'
      }`}>
        {live ? <><Radio className="w-3.5 h-3.5 animate-pulse" /> On now</> : 'Your next match'}
      </div>

      <div className={`mt-1.5 text-lg font-bold leading-tight ${live ? 'text-gray-900' : 'text-white'}`}>
        vs {opponent}
      </div>

      <div className={`flex flex-wrap items-center gap-x-4 gap-y-1 mt-2 text-[11px] ${
        live ? 'text-red-900' : 'text-emerald-100'
      }`}>
        <span className="flex items-center gap-1 font-bold">
          <MapPin className="w-3.5 h-3.5" /> Board {next.boardNumber}
        </span>
        <span className="flex items-center gap-1">
          <Clock className="w-3.5 h-3.5" />
          {next.scheduledTime || 'time to be set'}
          {next.scheduledDate ? ` · ${next.scheduledDate}` : ''}
        </span>
        <span className="capitalize opacity-90">{next.type} · {next.roundName}</span>
      </div>

      <div className={`flex items-center justify-between mt-3 pt-2 border-t text-[11px] ${
        live ? 'border-red-200 text-red-800' : 'border-emerald-800/60 text-emerald-200'
      }`}>
        <span>{played} of {mine.length} of your matches played</span>
        <ChevronRight className="w-4 h-4" />
      </div>
    </button>
  );
};
