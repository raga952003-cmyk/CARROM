import React from 'react';
import { 
  Trophy, 
  ChevronRight, 
  Crown, 
  Clock, 
  ArrowRight,
  Flame,
  Award,
  Sparkles
} from 'lucide-react';
import { Tournament, Match } from '../../types/tournament';

interface KnockoutBracketViewProps {
  tournament: Tournament;
  onOpenMatch?: (match: Match) => void;
}

export const KnockoutBracketView: React.FC<KnockoutBracketViewProps> = ({
  tournament,
  onOpenMatch
}) => {
  const knockoutMatches = tournament.matches.filter(m => m.stage === 'knockout');

  // Group by roundName (e.g. Round of 16, Quarter Final, Semi Final, Final)
  const groupedRounds: { [key: string]: Match[] } = {};
  knockoutMatches.forEach(m => {
    const rName = m.roundName || 'Knockout Round';
    if (!groupedRounds[rName]) groupedRounds[rName] = [];
    groupedRounds[rName].push(m);
  });

  // Order by the engine's roundIndex rather than by matching round names.
  // A name-based list cannot rank "Round of 16" or "Round 2", which were
  // sorted after the Final.
  const roundPosition: { [key: string]: number } = {};
  Object.entries(groupedRounds).forEach(([name, matches]) => {
    roundPosition[name] = Math.min(...matches.map(m => m.roundIndex ?? 99));
  });

  const sortedRoundKeys = Object.keys(groupedRounds).sort(
    (a, b) => roundPosition[a] - roundPosition[b]
  );

  // Byes mean a round can hold fewer matches than its bracket slot count, so
  // matches are ordered within a round by their drawn position.
  Object.values(groupedRounds).forEach(matches =>
    matches.sort(
      (a, b) =>
        (a.bracketPosition?.matchIndex ?? a.matchNumber) -
        (b.bracketPosition?.matchIndex ?? b.matchNumber)
    )
  );

  if (knockoutMatches.length === 0) {
    return (
      <div id="knockout-bracket-empty" className="bg-white rounded-2xl border border-gray-200/80 p-12 text-center shadow-xs">
        <Trophy className="w-12 h-12 text-gray-300 mx-auto mb-3" />
        <h4 className="text-base font-bold text-gray-900 mb-1">
          Knockout Bracket Not Active Yet
        </h4>
        <p className="text-xs text-gray-500 max-w-md mx-auto">
          {tournament.format === 'league_knockout'
            ? 'Knockout fixtures will automatically unlock once the League / Group stage matches are finalized and top seeds qualify.'
            : 'Knockout fixtures will be generated once registrations close.'}
        </p>
      </div>
    );
  }

  return (
    <div id="knockout-bracket-view" className="space-y-4">
      
      {/* Header Info */}
      <div className="bg-white p-5 rounded-2xl border border-gray-200/80 shadow-xs flex items-center justify-between">
        <div>
          <div className="flex items-center space-x-2">
            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-[#0B5D3B] text-white uppercase tracking-wider">
              <Trophy className="w-3 h-3 text-[#D4A72C]" />
              Knockout Tree
            </span>
            <span className="text-xs text-gray-500 font-medium">
              Single-Elimination Championship Pathway
            </span>
          </div>

          <h3 className="text-lg font-serif font-bold text-gray-900 mt-1">
            Championship Knockout Progression
          </h3>
          <p className="text-xs text-gray-600">
            Winners automatically advance to the next round upon confirming match results.
          </p>
        </div>
      </div>

      {/* Interactive Horizontal Bracket Flow */}
      <div className="bg-gradient-to-b from-gray-50 to-white rounded-3xl p-6 border border-gray-200/80 shadow-xs overflow-x-auto">
        <div className="flex items-stretch justify-start min-w-[760px] gap-8">
          
          {sortedRoundKeys.map((roundName, roundIdx) => {
            const matchesInRound = groupedRounds[roundName];
            const isFinalRound = roundName.toLowerCase().includes('final') && !roundName.toLowerCase().includes('semi') && !roundName.toLowerCase().includes('quarter');

            return (
              <div key={roundName} className="flex-1 flex flex-col justify-around min-w-[260px]">
                
                {/* Round Header Title */}
                <div className="text-center mb-4">
                  <span className={`inline-block px-3 py-1 rounded-full text-xs font-bold uppercase tracking-wider ${
                    isFinalRound
                      ? 'bg-[#D4A72C] text-[#202522] shadow-sm ring-2 ring-[#D4A72C]/30'
                      : 'bg-[#0B5D3B] text-white'
                  }`}>
                    {isFinalRound ? '🏆 ' + roundName : roundName}
                  </span>
                </div>

                {/* Match Cards in this Column */}
                <div className="space-y-6 flex-1 flex flex-col justify-around py-2">
                  {matchesInRound.map(match => {
                    const isCompleted = match.status === 'completed';
                    const isLive = match.status === 'live';

                    return (
                      <div
                        key={match.id}
                        onClick={() => onOpenMatch && onOpenMatch(match)}
                        className={`bg-white rounded-2xl p-3.5 border transition-all cursor-pointer shadow-xs hover:shadow-md relative ${
                          isLive
                            ? 'border-orange-500 ring-2 ring-orange-500/20'
                            : isCompleted
                            ? 'border-gray-200'
                            : 'border-dashed border-gray-300 hover:border-emerald-500'
                        }`}
                      >
                        {/* Board & Status Pill */}
                        <div className="flex items-center justify-between text-[10px] text-gray-500 mb-2 pb-1.5 border-b border-gray-100 font-semibold">
                          <span className="text-[#0B5D3B]">Board #{match.boardNumber}</span>
                          <span className={`px-2 py-0.2 rounded font-bold uppercase ${
                            isLive ? 'bg-orange-500 text-white' :
                            isCompleted ? 'bg-emerald-100 text-emerald-800' :
                            'bg-gray-100 text-gray-600'
                          }`}>
                            {match.status}
                          </span>
                        </div>

                        {/* Player 1 */}
                        <div className={`flex items-center justify-between p-2 rounded-xl text-xs transition-colors mb-1.5 ${
                          match.winnerId === match.player1Id
                            ? 'bg-emerald-50 font-bold text-emerald-950 border border-emerald-300/80'
                            : 'bg-gray-50 text-gray-800'
                        }`}>
                          <span className="truncate pr-2">{match.player1Name}</span>
                          <div className="flex items-center space-x-1.5 shrink-0">
                            <span className="font-bold">{match.player1TotalPoints}</span>
                            {match.winnerId === match.player1Id && (
                              <Crown className="w-3.5 h-3.5 text-[#D4A72C]" />
                            )}
                          </div>
                        </div>

                        {/* Player 2 */}
                        <div className={`flex items-center justify-between p-2 rounded-xl text-xs transition-colors ${
                          match.winnerId === match.player2Id
                            ? 'bg-emerald-50 font-bold text-emerald-950 border border-emerald-300/80'
                            : 'bg-gray-50 text-gray-800'
                        }`}>
                          <span className="truncate pr-2">{match.player2Name}</span>
                          <div className="flex items-center space-x-1.5 shrink-0">
                            <span className="font-bold">{match.player2TotalPoints}</span>
                            {match.winnerId === match.player2Id && (
                              <Crown className="w-3.5 h-3.5 text-[#D4A72C]" />
                            )}
                          </div>
                        </div>

                        {/* Match Result Summary */}
                        {isCompleted && (
                          <div className="mt-2 text-[10px] text-center font-bold text-emerald-800 bg-emerald-50/60 py-1 rounded-lg">
                            {match.winnerName} advanced ({match.player1BoardWins} - {match.player2BoardWins})
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>

              </div>
            );
          })}

        </div>
      </div>

    </div>
  );
};
