import React, { useEffect, useState } from 'react';
import { Users, User, Trophy, Layers, Info } from 'lucide-react';
import { Tournament, Match, StandingsBreakdown } from '../../types/tournament';
import { useTournament } from '../../context/TournamentContext';
import { PointsTableView } from './PointsTableView';

interface StandingsSectionsProps {
  tournament: Tournament;
  onSelectMatch?: (match: Match) => void;
}

/**
 * Points tables for a tournament, one section per category and, where the
 * format uses groups, one table per group.
 *
 * Singles and doubles are separate competitions even inside a single
 * tournament, and a group stage is several separate mini-leagues, so a single
 * combined table would rank entrants who never played each other.
 */
export const StandingsSections: React.FC<StandingsSectionsProps> = ({
  tournament,
  onSelectMatch
}) => {
  const { fetchStandingsBreakdown } = useTournament();
  const [data, setData] = useState<StandingsBreakdown | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const confirmedCount = tournament.matches.filter(m => m.resultConfirmed).length;

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const result = await fetchStandingsBreakdown(tournament.id);
        if (!cancelled) {
          setData(result);
          setError('');
        }
      } catch (e: any) {
        if (!cancelled) setError(e?.message || 'Could not load standings.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    return () => { cancelled = true; };
  }, [tournament.id, confirmedCount]);

  if (loading && !data) {
    return (
      <div className="bg-white rounded-2xl border border-gray-200/80 p-10 text-center text-xs text-gray-500 shadow-xs">
        Loading official standings…
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-white rounded-2xl border border-red-200 p-10 text-center text-xs text-red-700 shadow-xs">
        <Info className="w-7 h-7 mx-auto text-red-300 mb-2" />
        {error}
      </div>
    );
  }

  const categories = data?.categories || [];

  if (categories.length === 0) {
    return (
      <div className="bg-white rounded-2xl border border-gray-200/80 p-10 text-center shadow-xs">
        <Trophy className="w-8 h-8 mx-auto text-gray-300 mb-2" />
        <p className="text-xs text-gray-500">
          No standings yet. Generate fixtures and confirm results to populate the tables.
        </p>
      </div>
    );
  }

  return (
    <div id="standings-sections" className="space-y-8">
      {categories.map(category => {
        const isDoubles = category.category === 'doubles';
        // With no groups the category is one table; otherwise one per group.
        const blocks = category.groups.length > 0
          ? category.groups
          : [{ group: undefined, standings: category.standings,
               participantCount: category.participantCount, matchCount: category.matchCount }];

        return (
          <section key={category.category} className="space-y-3">
            <div
              className={`flex flex-wrap items-center justify-between gap-2 px-4 py-3 rounded-2xl border ${
                isDoubles
                  ? 'bg-blue-50/70 border-blue-200'
                  : 'bg-emerald-50/70 border-emerald-200'
              }`}
            >
              <div className="flex items-center gap-2">
                {isDoubles
                  ? <Users className="w-4 h-4 text-blue-700" />
                  : <User className="w-4 h-4 text-[#0B5D3B]" />}
                <h3 className={`font-bold text-sm tracking-tight ${
                  isDoubles ? 'text-blue-900' : 'text-[#0B5D3B]'
                }`}>
                  {isDoubles ? 'Doubles' : 'Singles'} Standings
                </h3>
                <span className="text-[11px] text-gray-600 font-medium">
                  {category.participantCount} {isDoubles ? 'teams' : 'players'} ·{' '}
                  {category.matchCount} matches
                </span>
              </div>
              {category.groups.length > 0 && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider bg-white/80 border border-gray-200 text-gray-700">
                  <Layers className="w-3 h-3" />
                  {category.groups.length} groups
                </span>
              )}
            </div>

            {blocks.map((block, idx) => (
              <div key={block.group || idx} className="space-y-2">
                {block.group && (
                  <div className="flex items-center gap-2 px-1">
                    <span className="px-2.5 py-0.5 rounded-lg text-[11px] font-black tracking-wider bg-[#0B5D3B] text-white">
                      GROUP {block.group}
                    </span>
                    <span className="text-[11px] text-gray-500 font-medium">
                      {block.participantCount} entrants · {block.matchCount} matches
                    </span>
                  </div>
                )}
                <PointsTableView
                  tournament={tournament}
                  onSelectMatch={onSelectMatch}
                  rows={block.standings}
                />
              </div>
            ))}
          </section>
        );
      })}
    </div>
  );
};
