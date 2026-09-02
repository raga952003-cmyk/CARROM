import React, { useEffect, useState } from 'react';
import { compareMatches } from '../../utils/matchOrder';
import { Printer, ArrowLeft } from 'lucide-react';
import { Tournament, Match, StandingsBreakdown } from '../../types/tournament';
import { tournamentService } from '../../services/tournamentService';
import { exitToApp } from '../../utils/useHashRoute';

type SheetKind = 'boards' | 'fixtures' | 'standings' | 'all';

interface PrintSheetsProps {
  tournamentId: string;
  kind: SheetKind;
}

/**
 * Paper output for running a tournament on the day (spec 82).
 *
 * Three sheets, all reachable by URL so they can be opened in a tab and sent
 * straight to a printer:
 *
 *   boards     one page per board, its matches in playing order with blank
 *              score boxes for the scorer to fill in
 *   fixtures   the full draw, for the notice board
 *   standings  the points tables, for the wall chart
 *
 * Rendered without any app chrome; @media print in index.css drops the
 * toolbar and forces a page break between boards.
 */
export const PrintSheets: React.FC<PrintSheetsProps> = ({ tournamentId, kind }) => {
  const [tournament, setTournament] = useState<Tournament | null>(null);
  const [standings, setStandings] = useState<StandingsBreakdown | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [t, s] = await Promise.all([
          tournamentService.getTournamentById(tournamentId),
          tournamentService.getStandings(tournamentId).catch(() => null),
        ]);
        if (!cancelled) {
          setTournament(t);
          setStandings(s as StandingsBreakdown | null);
        }
      } catch (e: any) {
        if (!cancelled) setError(e?.message || 'Could not load the tournament.');
      }
    })();
    return () => { cancelled = true; };
  }, [tournamentId]);

  if (error) {
    return <div className="p-10 text-center text-sm text-red-700">{error}</div>;
  }
  if (!tournament) {
    return <div className="p-10 text-center text-sm text-gray-500">Preparing sheets…</div>;
  }

  const matches = tournament.matches || [];
  const showBoards = kind === 'boards' || kind === 'all';
  const showFixtures = kind === 'fixtures' || kind === 'all';
  const showStandings = kind === 'standings' || kind === 'all';

  // Group by board, each board's matches in the order they will be played.
  const byBoard = new Map<number, Match[]>();
  matches.forEach(m => {
    const board = m.boardNumber || 1;
    if (!byBoard.has(board)) byBoard.set(board, []);
    byBoard.get(board)!.push(m);
  });
  byBoard.forEach(list =>
    list.sort((a, b) =>
      compareMatches(a, b)
    )
  );
  const boards = [...byBoard.keys()].sort((a, b) => a - b);

  const Header = ({ title, subtitle }: { title: string; subtitle?: string }) => (
    <div className="border-b-2 border-black pb-2 mb-3">
      <div className="flex items-baseline justify-between gap-4">
        <h1 className="text-lg font-bold">{tournament.name}</h1>
        <span className="text-xs">{tournament.venue}, {tournament.city}</span>
      </div>
      <div className="flex items-baseline justify-between gap-4 mt-0.5">
        <h2 className="text-sm font-semibold uppercase tracking-wide">{title}</h2>
        {subtitle && <span className="text-[11px]">{subtitle}</span>}
      </div>
    </div>
  );

  return (
    <div className="bg-white text-black min-h-screen">
      {/* Toolbar — hidden when printing */}
      <div className="print:hidden sticky top-0 bg-gray-100 border-b border-gray-300 px-4 py-2 flex items-center justify-between gap-3">
        <button
          onClick={exitToApp}
          className="flex items-center gap-1.5 text-xs font-bold text-gray-700 hover:text-black"
        >
          <ArrowLeft className="w-4 h-4" /> Back to dashboard
        </button>
        <div className="text-xs text-gray-600 hidden sm:block">
          {matches.length} matches · {boards.length} boards
        </div>
        <button
          onClick={() => window.print()}
          className="flex items-center gap-1.5 bg-[#0B5D3B] text-white px-3 py-1.5 rounded-lg text-xs font-bold"
        >
          <Printer className="w-4 h-4" /> Print
        </button>
      </div>

      <div className="p-6 print:p-0 space-y-8">

        {/* ---------- One page per board ---------- */}
        {showBoards && boards.map(board => (
          <section key={board} className="print-page">
            <Header
              title={`Board ${board} — scoring sheet`}
              subtitle={`${byBoard.get(board)!.length} matches`}
            />
            <table className="w-full text-[11px] border-collapse">
              <thead>
                <tr className="border-b border-black">
                  <th className="text-left py-1 w-10">#</th>
                  <th className="text-left py-1 w-24">Time</th>
                  <th className="text-left py-1">Match</th>
                  <th className="text-center py-1 w-16">B1</th>
                  <th className="text-center py-1 w-16">B2</th>
                  <th className="text-center py-1 w-16">B3</th>
                  <th className="text-left py-1 w-32">Winner</th>
                  <th className="text-left py-1 w-24">Signature</th>
                </tr>
              </thead>
              <tbody>
                {byBoard.get(board)!.map(m => (
                  <tr key={m.id} className="border-b border-gray-400 align-top">
                    <td className="py-2 font-bold">{m.matchNumber}</td>
                    <td className="py-2">{m.scheduledTime || '—'}</td>
                    <td className="py-2">
                      <div className="font-semibold">{m.player1Name}</div>
                      <div className="text-gray-600">vs</div>
                      <div className="font-semibold">{m.player2Name}</div>
                      <div className="text-[9px] uppercase tracking-wide text-gray-600 mt-0.5">
                        {m.roundName} · {m.type}
                      </div>
                    </td>
                    {/* Blank boxes: the scorer writes each board's result here */}
                    <td className="py-2"><div className="h-10 border border-gray-500" /></td>
                    <td className="py-2"><div className="h-10 border border-gray-500" /></td>
                    <td className="py-2"><div className="h-10 border border-gray-500" /></td>
                    <td className="py-2"><div className="h-10 border-b border-gray-500" /></td>
                    <td className="py-2"><div className="h-10 border-b border-gray-500" /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        ))}

        {/* ---------- Full draw ---------- */}
        {showFixtures && (
          <section className="print-page">
            <Header title="Full draw" subtitle={`${matches.length} matches`} />
            <table className="w-full text-[10px] border-collapse">
              <thead>
                <tr className="border-b border-black">
                  <th className="text-left py-1 w-10">#</th>
                  <th className="text-left py-1">Round</th>
                  <th className="text-left py-1">Match</th>
                  <th className="text-left py-1 w-16">Board</th>
                  <th className="text-left py-1 w-28">Date</th>
                  <th className="text-left py-1 w-20">Time</th>
                  <th className="text-left py-1 w-28">Result</th>
                </tr>
              </thead>
              <tbody>
                {[...matches].sort((a, b) => a.matchNumber - b.matchNumber).map(m => (
                  <tr key={m.id} className="border-b border-gray-300">
                    <td className="py-1 font-bold">{m.matchNumber}</td>
                    <td className="py-1">{m.roundName}</td>
                    <td className="py-1">{m.player1Name} v {m.player2Name}</td>
                    <td className="py-1">{m.boardNumber}</td>
                    <td className="py-1">{m.scheduledDate || '—'}</td>
                    <td className="py-1">{m.scheduledTime || '—'}</td>
                    <td className="py-1">
                      {m.resultConfirmed
                        ? `${m.winnerName || 'Draw'} (${m.player1BoardWins}-${m.player2BoardWins})`
                        : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        {/* ---------- Points tables ---------- */}
        {showStandings && standings?.categories?.map(cat => {
          const blocks = cat.groups.length > 0
            ? cat.groups
            : [{ group: undefined, standings: cat.standings,
                 participantCount: cat.participantCount, matchCount: cat.matchCount }];
          return blocks.map((block, i) => (
            <section key={`${cat.category}-${block.group || i}`} className="print-page">
              <Header
                title={`${cat.category} standings${block.group ? ` — Group ${block.group}` : ''}`}
                subtitle={`${block.participantCount} entrants · ${block.matchCount} matches`}
              />
              <table className="w-full text-[11px] border-collapse">
                <thead>
                  <tr className="border-b border-black">
                    <th className="text-left py-1 w-10">#</th>
                    <th className="text-left py-1">Participant</th>
                    <th className="text-center py-1 w-10">P</th>
                    <th className="text-center py-1 w-10">W</th>
                    <th className="text-center py-1 w-10">D</th>
                    <th className="text-center py-1 w-10">L</th>
                    <th className="text-center py-1 w-12">BD</th>
                    <th className="text-center py-1 w-14">NSD</th>
                    <th className="text-center py-1 w-12 font-bold">Pts</th>
                  </tr>
                </thead>
                <tbody>
                  {block.standings.map(r => (
                    <tr key={r.participantId} className="border-b border-gray-300">
                      <td className="py-1 font-bold">{r.rank}</td>
                      <td className="py-1">{r.participantName}</td>
                      <td className="text-center py-1">{r.played}</td>
                      <td className="text-center py-1">{r.won}</td>
                      <td className="text-center py-1">{r.drawn}</td>
                      <td className="text-center py-1">{r.lost}</td>
                      <td className="text-center py-1">{r.boardDiff}</td>
                      <td className="text-center py-1">{r.scoreDiff}</td>
                      <td className="text-center py-1 font-bold">{r.points}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          ));
        })}
      </div>
    </div>
  );
};
