/**
 * Offline tests for the browser-side logic.
 *
 * No network, no dev server, no database — the modules are imported directly.
 *
 *     npx tsx tests/offline/test_pure.ts
 *
 * The parity section is the important one. boardScoring.ts says in its header
 * that it mirrors board_result() in the Python engine, and that the preview
 * disagreeing with the stored score is the one thing that must never happen.
 * backend/tests/offline/gen_parity_cases.py writes every case together with the
 * answer the SERVER gives; this replays them through previewBoard.
 */
import { readFileSync } from 'node:fs';
import { previewBoard } from '../../src/utils/boardScoring';
import { minutesOfDay, compareMatches } from '../../src/utils/matchOrder';
import { findMyMatches, opponentOf } from '../../src/utils/myMatches';
import { resourcesToRefresh, Resource } from '../../src/utils/refreshScope';

type Slot = { failed: number; ran: number; examples: string[] };
const RESULTS = new Map<string, Slot>();
const KNOWN = new Map<string, Slot>();

function record(map: Map<string, Slot>, label: string, ok: boolean, example = '') {
  let slot = map.get(label);
  if (!slot) { slot = { failed: 0, ran: 0, examples: [] }; map.set(label, slot); }
  slot.ran++;
  if (!ok) {
    slot.failed++;
    if (slot.examples.length < 3) slot.examples.push(example);
  }
}

const check = (label: string, ok: boolean, example = '') => record(RESULTS, label, ok, example);
const observe = (label: string, ok: boolean, example = '') => record(KNOWN, label, ok, example);

// ---------------------------------------------------------------------------
// Preview / server parity
// ---------------------------------------------------------------------------

function suiteParity() {
  const url = new URL('./parity-cases.json', import.meta.url);
  let cases: any[];
  try {
    cases = JSON.parse(readFileSync(url, 'utf8'));
  } catch (e: any) {
    check('the parity corpus is present (run gen_parity_cases.py)', false, String(e).slice(0, 160));
    return;
  }

  for (const c of cases) {
    const got = previewBoard(c.obs, c.rules);
    const want = c.server;
    const ctx = `${JSON.stringify(c.obs)} rules=${JSON.stringify(c.rules)} ` +
                `server=${JSON.stringify(want)} preview=${JSON.stringify({
                  p1: got.p1, p2: got.p2, base: got.base,
                  queenBonus: got.queenBonus, queenSide: got.queenSide,
                  queenStatus: got.queenStatus })}`;

    check('the umpire preview and the server agree on player 1', got.p1 === want.p1, ctx);
    check('the umpire preview and the server agree on player 2', got.p2 === want.p2, ctx);
    check('the umpire preview and the server agree on base points', got.base === want.base, ctx);
    check('the umpire preview and the server agree on the queen bonus',
          got.queenBonus === want.queenBonus, ctx);
    check('the umpire preview and the server agree on who the queen paid',
          got.queenSide === want.queenSide, ctx);
    check('the umpire preview and the server agree on the queen status',
          got.queenStatus === want.queenStatus, ctx);
    check('the preview never shows a negative score', got.p1 >= 0 && got.p2 >= 0, ctx);
  }
}

// ---------------------------------------------------------------------------
// Time parsing — every minute of the day, in every format the app stores
// ---------------------------------------------------------------------------

function suiteTimes() {
  for (let h = 0; h < 24; h++) {
    for (let m = 0; m < 60; m++) {
      const mm = String(m).padStart(2, '0');
      const expected = h * 60 + m;

      const h24 = `${h}:${mm}`;
      check('a 24-hour time parses to minutes since midnight',
            minutesOfDay(h24) === expected, `${h24} -> ${minutesOfDay(h24)} want ${expected}`);

      const padded = `${String(h).padStart(2, '0')}:${mm}`;
      check('a zero-padded 24-hour time parses the same',
            minutesOfDay(padded) === expected, `${padded} -> ${minutesOfDay(padded)}`);

      const hour12 = h % 12 === 0 ? 12 : h % 12;
      const suffix = h < 12 ? 'AM' : 'PM';
      const display = `${hour12}:${mm} ${suffix}`;
      check('the display format the app stores parses correctly',
            minutesOfDay(display) === expected,
            `${display} -> ${minutesOfDay(display)} want ${expected}`);

      const lower = `${hour12}:${mm} ${suffix.toLowerCase()}`;
      check('a lowercase meridiem parses the same',
            minutesOfDay(lower) === expected, `${lower} -> ${minutesOfDay(lower)}`);
    }
  }

  for (const bad of ['', '   ', 'noon', '9', '9:', ':30', 'abc:de', '25:00',
                     '12:60', '99:99', 'TBD', '-1:30']) {
    check('an unparseable time sorts last rather than to midnight',
          minutesOfDay(bad) === null, `${JSON.stringify(bad)} -> ${minutesOfDay(bad)}`);
  }
  for (const nullish of [null, undefined]) {
    check('a missing time is null, not zero',
          minutesOfDay(nullish as any) === null, String(nullish));
  }

  // The bug matchOrder.ts was written to fix: alphabetical ordering put the
  // afternoon before the morning.
  check('the afternoon sorts after the morning',
        (minutesOfDay('2:50 PM') as number) > (minutesOfDay('9:35 AM') as number),
        '2:50 PM vs 9:35 AM');
  check('noon and midnight are not confused',
        minutesOfDay('12:00 AM') === 0 && minutesOfDay('12:00 PM') === 720,
        `${minutesOfDay('12:00 AM')} / ${minutesOfDay('12:00 PM')}`);
}

// ---------------------------------------------------------------------------
// Match ordering
// ---------------------------------------------------------------------------

function suiteOrdering() {
  const times = ['9:35 AM', '2:50 PM', '9:15 PM', '12:00 PM', '12:00 AM', '', 'TBD'];
  const dates = ['2026-03-01', '2026-03-02', ''];
  const pool: any[] = [];
  let n = 1;
  for (const d of dates) for (const t of times) {
    pool.push({ scheduledDate: d, scheduledTime: t, matchNumber: n++ });
  }

  // Antisymmetry and consistency, over every ordered pair.
  for (const a of pool) for (const b of pool) {
    const ab = compareMatches(a, b);
    const ba = compareMatches(b, a);
    const ctx = `${JSON.stringify(a)} vs ${JSON.stringify(b)} -> ${ab}/${ba}`;
    check('the comparator is antisymmetric', Math.sign(ab) === -Math.sign(ba), ctx);
    if (a === b) check('an item compares equal to itself', ab === 0, ctx);
  }

  // Transitivity, over every ordered triple.
  for (const a of pool) for (const b of pool) for (const c of pool) {
    if (compareMatches(a, b) < 0 && compareMatches(b, c) < 0) {
      check('the comparator is transitive', compareMatches(a, c) < 0,
            `${JSON.stringify(a)} < ${JSON.stringify(b)} < ${JSON.stringify(c)}`);
    }
  }

  const sorted = [...pool].sort(compareMatches);
  for (let i = 1; i < sorted.length; i++) {
    check('sorting produces a non-decreasing sequence',
          compareMatches(sorted[i - 1], sorted[i]) <= 0,
          `${JSON.stringify(sorted[i - 1])} then ${JSON.stringify(sorted[i])}`);
  }

  // Date outranks time, so a dated fixture with no time still belongs on its
  // own day — the ordering claim only holds WITHIN a date.
  for (const d of dates.filter(Boolean)) {
    const sameDay = sorted.filter(m => m.scheduledDate === d);
    const lastTimed = sameDay.map(m => minutesOfDay(m.scheduledTime) !== null)
                             .lastIndexOf(true);
    const firstUntimed = sameDay.map(m => minutesOfDay(m.scheduledTime) === null)
                                .indexOf(true);
    check('within a day, a fixture with no time sorts after the timed ones',
          firstUntimed === -1 || lastTimed === -1 || firstUntimed > lastTimed,
          `date=${d} lastTimed=${lastTimed} firstUntimed=${firstUntimed}`);
  }

  const firstUndated = sorted.findIndex(m => !m.scheduledDate);
  const lastDated = sorted.map(m => !!m.scheduledDate).lastIndexOf(true);
  check('an undated fixture never sorts ahead of a dated one',
        firstUndated === -1 || firstUndated > lastDated,
        `lastDated=${lastDated} firstUndated=${firstUndated}`);
}

// ---------------------------------------------------------------------------
// "My matches"
// ---------------------------------------------------------------------------

function match(over: any = {}) {
  return {
    id: 'm' + (over.matchNumber ?? 1), matchNumber: 1,
    player1Id: null, player2Id: null, player1Name: '', player2Name: '',
    scheduledDate: '2026-03-01', scheduledTime: '9:00 AM',
    ...over,
  };
}

function suiteMyMatches() {
  const byId = {
    matches: [
      match({ matchNumber: 1, player1Id: 'u1', player1Name: 'Ragavendra S', player2Name: 'Other' }),
      match({ matchNumber: 2, player2Id: 'u1', player2Name: 'Ragavendra S', player1Name: 'Other' }),
      match({ matchNumber: 3, player1Id: 'zz', player1Name: 'Nobody', player2Name: 'Else' }),
    ],
    registrations: [],
  } as any;

  const mine = findMyMatches(byId, { id: 'u1', name: 'Ragavendra S' });
  check('a player sees every match their id appears in', mine.length === 2,
        `got ${mine.length}`);
  check('a player does not see other people fixtures',
        mine.every((m: any) => m.matchNumber !== 3), JSON.stringify(mine.map((m: any) => m.matchNumber)));

  // One person, several matches — explicitly the case the organiser asked about.
  const many = {
    matches: Array.from({ length: 19 }, (_, i) =>
      match({ matchNumber: i + 1, player1Id: 'u1', player1Name: 'Ragavendra S',
              player2Name: 'Opp ' + i, scheduledTime: `${(i % 12) + 1}:00 ${i < 12 ? 'AM' : 'PM'}` })),
    registrations: [],
  } as any;
  const all = findMyMatches(many, { id: 'u1', name: 'Ragavendra S' });
  check('a player entered in many matches sees all of them', all.length === 19,
        `got ${all.length} of 19`);
  for (let i = 1; i < all.length; i++) {
    check('a player fixtures come back in the order they will be played',
          compareMatches(all[i - 1], all[i]) <= 0,
          `${all[i - 1].scheduledTime} then ${all[i].scheduledTime}`);
  }

  // Doubles: the fixture carries the TEAM id, not the person id.
  const doubles = {
    matches: [match({ matchNumber: 1, player1Id: 't1', player1Name: 'Team One',
                      player2Name: 'Team Two' })],
    registrations: [
      { type: 'doubles', team: { id: 't1', player1: { id: 'u1' }, player2: { id: 'u2' } } },
    ],
  } as any;
  check('a doubles player sees the fixture drawn against their team',
        findMyMatches(doubles, { id: 'u2', name: 'Partner' }).length === 1,
        JSON.stringify(findMyMatches(doubles, { id: 'u2', name: 'Partner' })));

  // Name fallback, for an account whose login is not its roster row.
  const byName = {
    matches: [match({ matchNumber: 1, player1Name: 'Srinivasan S', player2Name: 'Other' })],
    registrations: [],
  } as any;
  check('a name fallback finds the fixture when no id matches',
        findMyMatches(byName, { id: 'unknown', name: 'Srinivasan S' }).length === 1);
  check('the name fallback does not match a different person by prefix',
        findMyMatches(byName, { id: 'unknown', name: 'Srinivas' }).length === 0,
        'Srinivas must not match Srinivasan S');
  check('the name fallback ignores case and surrounding space',
        findMyMatches(byName, { id: 'unknown', name: '  srinivasan s  ' }).length === 1);
  check('a user with neither id nor name sees nothing',
        findMyMatches(byName, { id: '', name: '' }).length === 0);
  check('a null user sees nothing', findMyMatches(byName, null).length === 0);
  check('an empty tournament yields nothing',
        findMyMatches({ matches: [], registrations: [] } as any, { id: 'u1' }).length === 0);

  // The live database holds roster names carrying the sheet's serial number.
  const prefixed = {
    matches: [match({ matchNumber: 1, player1Name: '2. Ragavendra S', player2Name: 'Other' })],
    registrations: [],
  } as any;
  observe('a roster name carrying its sheet ordinal is still matched by name',
          findMyMatches(prefixed, { id: 'unknown', name: 'Ragavendra S' }).length === 1,
          'login "Ragavendra S" vs roster "2. Ragavendra S" -> ' +
          findMyMatches(prefixed, { id: 'unknown', name: 'Ragavendra S' }).length + ' matches');

  // opponentOf
  const m1 = match({ player1Id: 'u1', player1Name: 'Me', player2Name: 'You' });
  check('the opponent of my match is the other side',
        opponentOf(m1 as any, { id: 'u1', name: 'Me' }) === 'You',
        opponentOf(m1 as any, { id: 'u1', name: 'Me' }));
  check('the opponent is resolved from the other slot too',
        opponentOf(m1 as any, { id: 'u2', name: 'You' }) === 'Me',
        opponentOf(m1 as any, { id: 'u2', name: 'You' }));
  const blank = match({ player1Id: 'u1', player1Name: 'Me', player2Name: '' });
  check('an unfilled opponent slot reads as TBD',
        opponentOf(blank as any, { id: 'u1', name: 'Me' }) === 'TBD',
        opponentOf(blank as any, { id: 'u1', name: 'Me' }));
}

// ---------------------------------------------------------------------------


// ---------------------------------------------------------------------------
// What a realtime change costs
// ---------------------------------------------------------------------------

function suiteRefreshScope() {
  const NOW = 10_000;

  // A board score touches matches and boards, and nothing else on the screen.
  check(
    'scoring a board re-reads the draw and nothing else',
    JSON.stringify(resourcesToRefresh(['matches', 'boards'], NOW, {})) ===
      JSON.stringify(['tournaments']),
    JSON.stringify(resourcesToRefresh(['matches', 'boards'], NOW, {}))
  );

  // An entry can create a profile and a team as part of the same write, and
  // nothing else ever brings those two back: profiles and teams are not in the
  // realtime publication, so no event names them.
  const entry = resourcesToRefresh(['registrations'], NOW, {});
  check(
    'an entry re-reads the draw, the saved pairs and the roster',
    entry.length === 3 && entry.includes('tournaments')
      && entry.includes('teams') && entry.includes('players'),
    JSON.stringify(entry)
  );

  check(
    'a notification re-reads the notifications alone',
    JSON.stringify(resourcesToRefresh(['notifications'], NOW, {})) ===
      JSON.stringify(['notifications']),
    JSON.stringify(resourcesToRefresh(['notifications'], NOW, {}))
  );

  check(
    'a table nothing on screen reads costs no request at all',
    resourcesToRefresh(['audit_logs'], NOW, {}).length === 0,
    JSON.stringify(resourcesToRefresh(['audit_logs'], NOW, {}))
  );

  // The echo of our own write: the mutation already re-read what it changed,
  // and that read went out after the change came back over the websocket.
  check(
    'the echo of our own write costs nothing',
    resourcesToRefresh(['matches'], NOW, { tournaments: NOW + 50 }).length === 0,
    'refreshed anyway'
  );

  // The case the first version got wrong. Our write and somebody else's land
  // in the same debounce window; our follow-up read was issued between them.
  // Stamped by the window's FIRST change this looked covered, and the other
  // person's change was dropped -- with a live websocket there is no poll to
  // pick it up later.
  const OURS = NOW;
  const OUR_READ = NOW + 40;
  const THEIRS = NOW + 90;
  check(
    'a change that arrives after our read still gets re-read',
    resourcesToRefresh(['matches'], THEIRS, { tournaments: OUR_READ }).length === 1,
    `ours ${OURS}, our read ${OUR_READ}, theirs ${THEIRS}`
  );

  // Which is the requirement this places on realtimeService: it must stamp the
  // window with its LAST change. Handing over the first instead is what made
  // the case above disappear, and this is the shape of that mistake.
  check(
    'stamping the window by its first change is what would drop it',
    resourcesToRefresh(['matches'], OURS, { tournaments: OUR_READ }).length === 0,
    'the first-change stamp no longer suppresses, so the guard has moved'
  );

  // A read issued at the same instant proves nothing about what it saw.
  check(
    'a read issued at the very instant of the change is not trusted',
    resourcesToRefresh(['matches'], NOW, { tournaments: NOW }).length === 1,
    'skipped on an equal timestamp'
  );

  // The pull on reconnect. The connection was down for an unknown stretch and
  // no stamp can speak for what happened during it, so it reconciles all four
  // reads -- including the two no event can ever name.
  const onConnect = resourcesToRefresh(
    ['tournaments', 'notifications'],
    0,
    { tournaments: NOW + 5_000, notifications: NOW + 5_000 },
  );
  check(
    'reconnecting re-reads everything, however recent the last read was',
    onConnect.length === 4,
    JSON.stringify(onConnect)
  );

  // Only the resource that moved is judged.
  const mixed = resourcesToRefresh(['matches', 'notifications'], NOW, {
    tournaments: NOW + 50,
  });
  check(
    'one resource being current does not suppress another that is not',
    JSON.stringify(mixed) === JSON.stringify(['notifications'] as Resource[]),
    JSON.stringify(mixed)
  );

  // The stamp means a read that SUCCEEDED. A resource that has never been read
  // successfully has no stamp, so nothing about it is ever suppressed --
  // which is what a failed read leaves behind.
  check(
    'a resource with no successful read behind it is always re-read',
    resourcesToRefresh(['matches'], NOW, { notifications: NOW + 500 }).length === 1,
    'a resource never read was treated as current'
  );
}

const SUITES: Array<[string, () => void]> = [
  ['refresh scope', suiteRefreshScope],
  ['preview/server parity', suiteParity],
  ['time parsing', suiteTimes],
  ['match ordering', suiteOrdering],
  ['my matches', suiteMyMatches],
];

function main() {
  for (const [name, fn] of SUITES) {
    try { fn(); } catch (e: any) {
      check(`suite ${name} ran to completion`, false, String(e && e.stack || e).slice(0, 400));
    }
  }

  let total = 0;
  for (const slot of RESULTS.values()) total += slot.ran;
  const failed = [...RESULTS.entries()].filter(([, s]) => s.failed).sort();

  console.log('='.repeat(78));
  console.log('offline frontend suite');
  console.log('='.repeat(78));
  console.log(`assertions executed : ${total}`);
  console.log(`invariants checked  : ${RESULTS.size}`);
  console.log(`invariants violated : ${failed.length}`);
  console.log();

  if (failed.length) {
    console.log('FAILURES');
    console.log('-'.repeat(78));
    for (const [label, slot] of failed) {
      console.log(`  ${label}`);
      console.log(`     ${slot.failed} of ${slot.ran} cases failed`);
      for (const ex of slot.examples) console.log(`     e.g. ${ex}`);
      console.log();
    }
  }

  const noted = [...KNOWN.entries()].filter(([, s]) => s.failed).sort();
  if (noted.length) {
    console.log('OBSERVATIONS (behaviour worth a decision, not asserted as bugs)');
    console.log('-'.repeat(78));
    for (const [label, slot] of noted) {
      console.log(`  ${label} -> ${slot.failed} of ${slot.ran}`);
      for (const ex of slot.examples) console.log(`     e.g. ${ex}`);
    }
    console.log();
  }

  process.exit(failed.length);
}

main();
