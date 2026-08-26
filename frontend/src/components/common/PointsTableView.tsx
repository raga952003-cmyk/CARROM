import React, { useState, useEffect } from 'react';
import { 
  Trophy, 
  ArrowUpDown, 
  ShieldCheck, 
  HelpCircle, 
  Info, 
  Award, 
  ChevronUp, 
  ChevronDown,
  Sparkles
} from 'lucide-react';
import { Tournament, StandingsRow, Match, Player, Team } from '../../types/tournament';
import { useTournament } from '../../context/TournamentContext';

interface PointsTableViewProps {
  tournament: Tournament;
  onSelectMatch?: (match: Match) => void;
  /** Render these rows instead of fetching. Used when a parent has already
   *  split the standings into per-category / per-group tables. */
  rows?: StandingsRow[];
}

export const PointsTableView: React.FC<PointsTableViewProps> = ({
  tournament,
  onSelectMatch,
  rows
}) => {
  const [sortField, setSortField] = useState<keyof StandingsRow>('points');
  const [sortAsc, setSortAsc] = useState<boolean>(false);
  const [showTiebreakerHelp, setShowTiebreakerHelp] = useState(false);

  // Standings are computed server-side from official, confirmed results
  // (spec 74). The browser renders them and can never write to them.
  const { fetchStandings } = useTournament();
  const [standings, setStandings] = useState<StandingsRow[]>([]);
  const [standingsError, setStandingsError] = useState<string>('');

  useEffect(() => {
    let cancelled = false;

    // A parent that already split the tables passes them in; only fetch when
    // this component is being used standalone.
    if (rows) {
      setStandings(rows);
      setStandingsError('');
      return;
    }

    const load = async () => {
      try {
        const fetched = await fetchStandings(tournament.id);
        if (!cancelled) {
          setStandings(fetched);
          setStandingsError('');
        }
      } catch (error: any) {
        if (!cancelled) {
          setStandingsError(error?.message || 'Could not load standings.');
        }
      }
    };

    load();
    return () => { cancelled = true; };
    // Recompute when confirmed results change, which is what moves the table.
  }, [tournament.id, rows, tournament.matches.filter(m => m.resultConfirmed).length]);

  const sortedStandings = [...standings].sort((a, b) => {
    if (sortField === 'rank') {
      return sortAsc ? a.rank - b.rank : b.rank - a.rank;
    }
    const valA = a[sortField];
    const valB = b[sortField];

    if (typeof valA === 'number' && typeof valB === 'number') {
      return sortAsc ? valA - valB : valB - valA;
    }
    return 0;
  });

  const handleSort = (field: keyof StandingsRow) => {
    if (sortField === field) {
      setSortAsc(!sortAsc);
    } else {
      setSortField(field);
      setSortAsc(false);
    }
  };

  const getQualificationBadge = (rank: number) => {
    if (tournament.format === 'league_knockout') {
      if (rank <= 2) {
        return (
          <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-100 text-emerald-900 border border-emerald-300">
            Qualified (Knockouts)
          </span>
        );
      } else if (rank <= 4) {
        return (
          <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-blue-50 text-blue-800">
            Playoff Contender
          </span>
        );
      }
    } else if (rank === 1) {
      return (
        <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-100 text-amber-900 border border-amber-300">
          Leader 👑
        </span>
      );
    }
    return null;
  };

  return (
    <div id="points-table-view" className="space-y-4">
      
      {/* Standings Summary & Tiebreaker Explanation */}
      <div className="bg-white p-5 rounded-2xl border border-gray-200/80 shadow-xs flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center space-x-2">
            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-[#0B5D3B] text-white uppercase tracking-wider">
              <Trophy className="w-3 h-3 text-[#D4A72C]" />
              Official League Standings
            </span>
            <span className="text-xs text-gray-500 font-medium">
              Auto-calculated from official board scores
            </span>
          </div>

          <h3 className="text-lg font-serif font-bold text-gray-900 mt-1">
            Tournament Points & Ranking Table
          </h3>
          <p className="text-xs text-gray-600">
            Deterministic ranking calculated in real time. Standard rules: Win = {tournament.rules.pointsForWin} pts, Draw = {tournament.rules.pointsForDraw} pt, Loss = {tournament.rules.pointsForLoss} pts.
          </p>
        </div>

        <button
          onClick={() => setShowTiebreakerHelp(!showTiebreakerHelp)}
          className="text-xs text-[#0B5D3B] hover:text-[#08472d] font-bold flex items-center gap-1 px-3 py-1.5 rounded-xl bg-emerald-50 border border-emerald-200 shrink-0 self-start md:self-auto"
        >
          <Info className="w-4 h-4" />
          <span>{showTiebreakerHelp ? 'Hide Tiebreaker Hierarchy' : 'View Tiebreaker Rules'}</span>
        </button>
      </div>

      {/* Tiebreaker Hierarchy Callout */}
      {showTiebreakerHelp && (
        <div className="bg-amber-50/80 border border-amber-200/90 rounded-2xl p-4 text-xs text-amber-950 animate-in fade-in duration-150">
          <div className="flex items-center space-x-2 font-bold mb-1">
            <ShieldCheck className="w-4 h-4 text-[#0B5D3B]" />
            <span>Official Carrom Federation Deterministic Tiebreaker Order:</span>
          </div>
          <ol className="list-decimal list-inside space-y-1 text-[11px] text-amber-900 ml-1">
            <li><strong>Total Match Points</strong> (Won matches × {tournament.rules.pointsForWin} + Drawn matches × {tournament.rules.pointsForDraw})</li>
            <li><strong>Board Wins Difference (BD)</strong> = Total Boards Won − Total Boards Lost</li>
            <li><strong>Net Score Difference (NSD)</strong> = Total Game Points Scored − Total Game Points Conceded</li>
            <li><strong>Head-to-Head Result</strong> between the tied competitors</li>
          </ol>
        </div>
      )}

      {/* Points Table */}
      <div className="bg-white rounded-2xl border border-gray-200/80 shadow-xs overflow-hidden">
        {/* Table Banner Header */}
        <div className="p-4 bg-[#0B5D3B] text-white flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center space-x-2">
            <Trophy className="w-4 h-4 text-[#D4A72C]" />
            <span className="font-bold text-sm tracking-tight">Standings & Federation Points</span>
          </div>
          <span className="text-[10px] text-[#D4A72C] font-bold uppercase tracking-wider">
            Deterministic Tiebreakers Active
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-gray-50 border-b border-gray-200 text-gray-600 font-bold text-[10px] uppercase tracking-wider">
              <tr>
                <th 
                  onClick={() => handleSort('rank')}
                  className="px-4 py-3 cursor-pointer hover:bg-gray-100 transition-colors w-16"
                >
                  <div className="flex items-center gap-1">
                    <span>Rank</span>
                    <ArrowUpDown className="w-3 h-3 text-[#0B5D3B]" />
                  </div>
                </th>

                <th className="px-4 py-3">Participant / Club</th>

                <th 
                  onClick={() => handleSort('played')}
                  className="px-3 py-3 text-center cursor-pointer hover:bg-gray-100 transition-colors"
                  title="Matches Played"
                >
                  P
                </th>

                <th 
                  onClick={() => handleSort('won')}
                  className="px-3 py-3 text-center cursor-pointer hover:bg-gray-100 transition-colors"
                  title="Matches Won"
                >
                  W
                </th>

                <th 
                  onClick={() => handleSort('drawn')}
                  className="px-3 py-3 text-center cursor-pointer hover:bg-gray-100 transition-colors"
                  title="Matches Drawn"
                >
                  D
                </th>

                <th 
                  onClick={() => handleSort('lost')}
                  className="px-3 py-3 text-center cursor-pointer hover:bg-gray-100 transition-colors"
                  title="Matches Lost"
                >
                  L
                </th>

                <th 
                  onClick={() => handleSort('boardWins')}
                  className="px-3 py-3 text-center cursor-pointer hover:bg-gray-100 transition-colors"
                  title="Board Wins"
                >
                  BW
                </th>

                <th 
                  onClick={() => handleSort('boardLosses')}
                  className="px-3 py-3 text-center cursor-pointer hover:bg-gray-100 transition-colors"
                  title="Board Losses"
                >
                  BL
                </th>

                <th 
                  onClick={() => handleSort('boardDiff')}
                  className="px-3 py-3 text-center cursor-pointer hover:bg-gray-100 transition-colors"
                  title="Board Difference (BW - BL)"
                >
                  BD
                </th>

                <th 
                  onClick={() => handleSort('scoreDiff')}
                  className="px-3 py-3 text-center cursor-pointer hover:bg-gray-100 transition-colors"
                  title="Net Score Difference (Pts For - Pts Against)"
                >
                  NSD
                </th>

                <th 
                  onClick={() => handleSort('points')}
                  className="px-4 py-3 text-center cursor-pointer bg-emerald-50 hover:bg-emerald-100 transition-colors"
                  title="Total Championship Points"
                >
                  <div className="flex items-center justify-center gap-1 text-[#0B5D3B] font-bold">
                    <span>PTS</span>
                    <ArrowUpDown className="w-3 h-3 text-[#D4A72C]" />
                  </div>
                </th>

                <th className="px-4 py-3 text-right">Progression Status</th>
              </tr>
            </thead>

            <tbody className="divide-y divide-gray-100">
              {standingsError ? (
                <tr>
                  <td colSpan={12} className="text-center py-12 text-red-600">
                    <Info className="w-8 h-8 mx-auto text-red-300 mb-2" />
                    {standingsError}
                  </td>
                </tr>
              ) : sortedStandings.length === 0 ? (
                <tr>
                  <td colSpan={12} className="text-center py-12 text-gray-500">
                    <Trophy className="w-8 h-8 mx-auto text-gray-300 mb-2" />
                    No match standings recorded yet. Start matches and confirm scores to populate standings.
                  </td>
                </tr>
              ) : (
                sortedStandings.map((row) => (
                  <tr 
                    key={row.participantId} 
                    className={`hover:bg-gray-50/90 transition-colors ${
                      row.rank <= 2 ? 'bg-emerald-50/20 font-medium' : ''
                    }`}
                  >
                    <td className="px-4 py-3 font-bold text-gray-900">
                      <div className="flex items-center gap-1.5">
                        <span className={`w-6 h-6 rounded-full flex items-center justify-center text-xs ${
                          row.rank === 1 ? 'bg-[#D4A72C] text-[#202522] font-black shadow-xs' :
                          row.rank === 2 ? 'bg-gray-200 text-gray-800 font-bold' :
                          row.rank === 3 ? 'bg-amber-100 text-amber-800 font-bold' :
                          'text-gray-500'
                        }`}>
                          {row.rank}
                        </span>
                      </div>
                    </td>

                    <td className="px-4 py-3">
                      <div className="font-bold text-gray-900">{row.participantName}</div>
                      <div className="text-[10px] text-gray-400">
                        {row.participantType === 'singles' ? 'Singles Competitor' : 'Doubles Team'}
                      </div>
                    </td>

                    <td className="px-3 py-3 text-center text-gray-700 font-semibold">{row.played}</td>
                    <td className="px-3 py-3 text-center text-emerald-700 font-bold">{row.won}</td>
                    <td className="px-3 py-3 text-center text-gray-500 font-medium">{row.drawn}</td>
                    <td className="px-3 py-3 text-center text-red-600 font-medium">{row.lost}</td>
                    <td className="px-3 py-3 text-center text-gray-700">{row.boardWins}</td>
                    <td className="px-3 py-3 text-center text-gray-700">{row.boardLosses}</td>
                    <td className="px-3 py-3 text-center font-bold text-gray-800">
                      {row.boardDiff > 0 ? `+${row.boardDiff}` : row.boardDiff}
                    </td>
                    <td className="px-3 py-3 text-center font-medium text-gray-600">
                      {row.scoreDiff > 0 ? `+${row.scoreDiff}` : row.scoreDiff}
                    </td>

                    <td className="px-4 py-3 text-center bg-emerald-50/50">
                      <span className="text-base font-black text-[#0B5D3B]">
                        {row.points}
                      </span>
                    </td>

                    <td className="px-4 py-3 text-right">
                      {getQualificationBadge(row.rank)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

    </div>
  );
};
