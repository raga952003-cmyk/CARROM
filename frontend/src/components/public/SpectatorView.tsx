import React, { useEffect, useMemo, useState, useRef } from 'react';
import { compareMatches } from '../../utils/matchOrder';
import { Trophy, Clock, MapPin, Search, RefreshCw, Radio } from 'lucide-react';
import { Tournament, Match, StandingsBreakdown } from '../../types/tournament';
import { tournamentService } from '../../services/tournamentService';
import { subscribeToTournamentData } from '../../services/realtimeService';

interface SpectatorViewProps {
  tournamentId?: string;
}

/**
 * Public tournament view — no sign-in (spec 64: published information).
 *
 * Every read endpoint it uses is already public, so this needs no session.
 * Players use it to answer "when and where do I play next?" without an
 * account, which is what most people at a venue actually want.
 */
export const SpectatorView: React.FC<SpectatorViewProps> = ({ tournamentId }) => {
  const [tournaments, setTournaments] = useState<Tournament[]>([]);
  const [selectedId, setSelectedId] = useState<string>(tournamentId || '');
  const [standings, setStandings] = useState<StandingsBreakdown | null>(null);
  const [query, setQuery] = useState('');
  const [tab, setTab] = useState<'schedule' | 'standings'>('schedule');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const loadStandings = async (id: string) => {
    if (!id) return;
    setStandings(await tournamentService.getStandings(id).catch(() => null) as any);
  };

  // Whether a load is under way or done, so the subscription's pull on connect
  // does not repeat one this view has already started for itself.
  //
  // Started, not finished: the socket comes up in a few hundred milliseconds
  // and reading the whole draw takes longer than that, so a flag set on
  // completion would still be false when the pull arrives and would skip
  // nothing. Cleared again if the load fails, so the pull is still there to
  // rescue a screen that has nothing on it.
  const started = useRef(false);

  const load = async () => {
    started.current = true;
    try {
      const list = await tournamentService.getAllTournaments();
      setTournaments(list);
      const id = selectedId || tournamentId || list[0]?.id || '';
      if (id !== selectedId) setSelectedId(id);
      await loadStandings(id);
      setError('');
    } catch (e: any) {
      started.current = false;
      setError(e?.message || 'Could not load tournaments.');
    } finally {
      setLoading(false);
    }
  };

  // The draw, once. This used to be keyed on `selectedId`, which load() sets
  // itself the first time it runs -- so opening the board fetched every
  // tournament, every fixture and every board, then immediately did it again
  // because resolving the selection had changed the dependency. The heaviest
  // read in the app, twice, on every visit to a public page.
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  // Switching tournament needs nothing but its table: the list response
  // already carries every tournament's fixtures.
  useEffect(() => { loadStandings(selectedId); /* eslint-disable-next-line */ }, [selectedId]);

  // Live updates without a session: the anon key plus RLS is exactly what
  // Realtime is for -- when it is configured. It was not, on the deployed site,
  // because the build had no VITE_SUPABASE_* variables, so the subscription was
  // a no-op and this page sat on a snapshot under a heading reading "LIVE".
  //
  // The signed-in app already polls when Realtime is unavailable; a spectator
  // had no such fallback. They do now, and the page only claims to be live when
  // the socket says so. Worth keeping even once Realtime works: a phone on
  // venue wifi drops the connection.
  const [isLive, setIsLive] = useState(false);

  useEffect(() => {
    let poll: ReturnType<typeof setInterval> | null = null;
    const stopPolling = () => { if (poll) { clearInterval(poll); poll = null; } };

    const handle = subscribeToTournamentData({
      onChange: ({ observedAt }) => {
        // observedAt 0 is the pull the subscription does on connect, so a
        // screen that opens with the socket is current from its first frame.
        // This one already loaded on mount a moment earlier, so taking that
        // pull as well read the entire draw twice for every visitor. A real
        // change still reloads, and a mount that has not landed is not skipped.
        if (observedAt || !started.current) load();
      },
      onStatus: status => {
        const live = status === 'live';
        setIsLive(live);
        if (live) stopPolling();
        else if (!poll) poll = setInterval(load, 20000);
      },
    });

    // Nothing calls onStatus at all when Supabase is unconfigured, so the
    // fallback cannot wait to be told.
    const kickoff = setTimeout(() => { if (!poll) poll = setInterval(load, 20000); }, 3000);

    return () => {
      clearTimeout(kickoff);
      stopPolling();
      handle.unsubscribe();
    };
    // eslint-disable-next-line
  }, [selectedId]);

  const tournament = tournaments.find(t => t.id === selectedId);

  const matches = useMemo(() => {
    const all = tournament?.matches || [];
    const needle = query.trim().toLowerCase();
    const filtered = needle
      ? all.filter(m =>
          m.player1Name.toLowerCase().includes(needle) ||
          m.player2Name.toLowerCase().includes(needle))
      : all;
    return [...filtered].sort((a, b) =>
      compareMatches(a, b));
  }, [tournament, query]);

  const live = matches.filter(m => m.status === 'live');
  const upcoming = matches.filter(m => !m.resultConfirmed && m.status !== 'live').slice(0, 40);
  const done = matches.filter(m => m.resultConfirmed).slice(-20).reverse();

  if (loading) {
    return <Centered>Loading tournament…</Centered>;
  }
  if (error) {
    return <Centered tone="error">{error}</Centered>;
  }
  if (!tournament) {
    return <Centered>No tournaments have been published yet.</Centered>;
  }

  return (
    <div className="min-h-screen bg-[#F8F6F0] text-[#202522]">
      <header className="bg-[#0B5D3B] text-white">
        <div className="max-w-3xl mx-auto px-4 py-4">
          <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-widest text-[#D4A72C]">
            <Trophy className="w-3.5 h-3.5" />
            <span>Tournament board</span>
            {/* Only claimed when the socket is actually connected. Saying
                "live" over a snapshot is worse than saying nothing. */}
            <span className={`flex items-center gap-1 ${isLive ? 'text-emerald-200' : 'text-emerald-300/70'}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${isLive ? 'bg-emerald-300 animate-pulse' : 'bg-emerald-400/50'}`} />
              {isLive ? 'Live' : 'Updating every 20s'}
            </span>
          </div>
          <h1 className="text-xl font-serif font-bold mt-1 leading-tight">{tournament.name}</h1>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-emerald-100 mt-1">
            <span className="flex items-center gap-1"><MapPin className="w-3 h-3" />{tournament.venue}, {tournament.city}</span>
            <span className="flex items-center gap-1"><Clock className="w-3 h-3" />{tournament.tournamentStartDate} – {tournament.tournamentEndDate}</span>
          </div>

          {tournaments.length > 1 && (
            <select
              value={selectedId}
              onChange={e => { setSelectedId(e.target.value); setLoading(true); }}
              className="mt-3 w-full bg-emerald-950/70 border border-emerald-800 rounded-xl px-3 py-2 text-xs text-white"
            >
              {tournaments.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          )}
        </div>
      </header>

      <div className="max-w-3xl mx-auto px-4 py-4 space-y-4">
        <div className="relative">
          <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Find your name to see your matches"
            className="w-full pl-9 pr-3 py-2.5 rounded-xl border border-gray-200 bg-white text-sm"
          />
        </div>

        <div className="flex gap-2">
          {(['schedule', 'standings'] as const).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`flex-1 py-2 rounded-xl text-xs font-bold capitalize border ${
                tab === t ? 'bg-[#0B5D3B] text-white border-[#0B5D3B]' : 'bg-white text-gray-600 border-gray-200'
              }`}
            >
              {t}
            </button>
          ))}
        </div>

        {tab === 'schedule' ? (
          <>
            {live.length > 0 && (
              <Section title="Playing now" accent>
                {live.map(m => <MatchRow key={m.id} match={m} live />)}
              </Section>
            )}
            <Section title={query ? `Upcoming for “${query}”` : 'Upcoming'}>
              {upcoming.length === 0
                ? <Empty>Nothing scheduled{query ? ' for that name' : ''}.</Empty>
                : upcoming.map(m => <MatchRow key={m.id} match={m} />)}
            </Section>
            {done.length > 0 && (
              <Section title="Recent results">
                {done.map(m => <MatchRow key={m.id} match={m} />)}
              </Section>
            )}
          </>
        ) : (
          <>
            {(standings?.categories || []).map(cat => {
              const blocks = cat.groups.length > 0
                ? cat.groups
                : [{ group: undefined, standings: cat.standings,
                     participantCount: cat.participantCount, matchCount: cat.matchCount }];
              return (
                <div key={cat.category} className="space-y-2">
                  <h2 className="text-xs font-black uppercase tracking-wider text-[#0B5D3B]">
                    {cat.category} · {cat.participantCount} entrants
                  </h2>
                  {blocks.map((b, i) => (
                    <div key={b.group || i} className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
                      {b.group && (
                        <div className="px-3 py-1.5 bg-gray-50 border-b border-gray-200 text-[11px] font-black">
                          GROUP {b.group}
                        </div>
                      )}
                      <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                          <thead className="bg-gray-50 text-[10px] uppercase text-gray-500">
                            <tr>
                              <th className="px-3 py-2 text-left">#</th>
                              <th className="px-3 py-2 text-left">Participant</th>
                              <th className="px-2 py-2">P</th>
                              <th className="px-2 py-2">W</th>
                              <th className="px-2 py-2">L</th>
                              <th className="px-2 py-2 font-bold">Pts</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-gray-100">
                            {b.standings.map(r => (
                              <tr key={r.participantId}
                                  className={query && r.participantName.toLowerCase().includes(query.toLowerCase())
                                    ? 'bg-amber-50' : ''}>
                                <td className="px-3 py-2 font-bold">{r.rank}</td>
                                <td className="px-3 py-2">{r.participantName}</td>
                                <td className="px-2 py-2 text-center">{r.played}</td>
                                <td className="px-2 py-2 text-center">{r.won}</td>
                                <td className="px-2 py-2 text-center">{r.lost}</td>
                                <td className="px-2 py-2 text-center font-bold">{r.points}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  ))}
                </div>
              );
            })}
            {!standings?.categories?.length && <Empty>No standings yet.</Empty>}
          </>
        )}

        <button
          onClick={load}
          className="w-full py-2.5 rounded-xl border border-gray-200 bg-white text-xs font-bold text-gray-600 flex items-center justify-center gap-1.5"
        >
          <RefreshCw className="w-3.5 h-3.5" /> Refresh
        </button>
      </div>
    </div>
  );
};

const Section: React.FC<{ title: string; accent?: boolean; children: React.ReactNode }> =
  ({ title, accent, children }) => (
    <section className="space-y-1.5">
      <h2 className={`text-xs font-black uppercase tracking-wider flex items-center gap-1.5 ${
        accent ? 'text-red-700' : 'text-gray-500'}`}>
        {accent && <Radio className="w-3.5 h-3.5 animate-pulse" />}
        {title}
      </h2>
      <div className="space-y-1.5">{children}</div>
    </section>
  );

const MatchRow: React.FC<{ match: Match; live?: boolean }> = ({ match, live }) => (
  <div className={`bg-white rounded-xl border p-3 ${live ? 'border-red-300' : 'border-gray-200'}`}>
    <div className="flex items-center justify-between text-[10px] uppercase tracking-wider text-gray-500">
      <span>#{match.matchNumber} · {match.roundName}</span>
      <span className="font-bold text-[#0B5D3B]">Board {match.boardNumber}</span>
    </div>
    <div className="mt-1 flex items-center justify-between gap-3">
      <div className="min-w-0 text-sm">
        <div className={`truncate ${match.winnerName === match.player1Name ? 'font-black' : 'font-semibold'}`}>
          {match.player1Name}
        </div>
        <div className={`truncate ${match.winnerName === match.player2Name ? 'font-black' : 'font-semibold'}`}>
          {match.player2Name}
        </div>
      </div>
      <div className="text-right shrink-0">
        {/* Three states, not two. A match in play used to fall through to the
            scheduled time -- which is empty for every match in this tournament,
            so the one thing a spectator came to see rendered as a dash while
            the umpire was recording boards a metre away. */}
        {match.resultConfirmed ? (
          <div className="text-sm font-black">{match.player1BoardWins}–{match.player2BoardWins}</div>
        ) : match.status === 'live' || match.status === 'paused' ? (
          <>
            <div className="text-sm font-black text-[#0B5D3B]">
              {match.player1BoardWins}–{match.player2BoardWins}
            </div>
            {(() => {
              const playing = (match.boards || []).find(b => b.status === 'in_progress');
              if (!playing) return null;
              return (
                <div className="text-[10px] text-gray-600 font-semibold">
                  Board {playing.boardNumber}: {playing.player1Score}–{playing.player2Score}
                </div>
              );
            })()}
          </>
        ) : (
          <div className="text-[11px] text-gray-600">{match.scheduledTime || '—'}</div>
        )}
        <div className="text-[10px] text-gray-400">{match.scheduledDate}</div>
      </div>
    </div>
  </div>
);

const Empty: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="bg-white rounded-xl border border-gray-200 p-6 text-center text-xs text-gray-500">
    {children}
  </div>
);

const Centered: React.FC<{ children: React.ReactNode; tone?: 'error' }> = ({ children, tone }) => (
  <div className="min-h-screen bg-[#F8F6F0] flex items-center justify-center p-6">
    <p className={`text-sm ${tone === 'error' ? 'text-red-700' : 'text-gray-500'}`}>{children}</p>
  </div>
);
