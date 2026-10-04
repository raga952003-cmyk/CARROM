import assert from 'node:assert/strict';
import { createRefreshQueue, RefreshScope } from '../../src/utils/refreshQueue';

const calls: RefreshScope[] = [];
let release: () => void = () => {};
const firstRead = new Promise<void>(resolve => { release = resolve; });
const read = createRefreshQueue<'tournaments' | 'notifications'>(async (key, scope) => {
  if (key === 'notifications') return;
  calls.push(scope);
  if (calls.length === 1) await firstRead;
});
const first = read('tournaments', ['A']);
const second = read('tournaments', ['B']);
const third = read('tournaments', ['C']);
assert.equal(second, third, 'Concurrent callers share one follow-up');
assert.deepEqual(calls, [['A']], 'No overlapping read can overwrite a newer result');
await read('notifications');
release();
await Promise.all([first, second, third]);
assert.deepEqual(calls, [['A'], ['B', 'C']], 'Every event requested during the first read is refreshed');

const fullCalls: RefreshScope[] = [];
let releaseFull: () => void = () => {};
const fullGate = new Promise<void>(resolve => { releaseFull = resolve; });
const full = createRefreshQueue<'tournaments'>(async (_key, scope) => {
  fullCalls.push(scope);
  if (fullCalls.length === 1) await fullGate;
});
const pending = full('tournaments', ['A']);
const scoped = full('tournaments', ['B']);
const reconnect = full('tournaments');
full('tournaments', ['C']);
releaseFull();
await Promise.all([pending, scoped, reconnect]);
assert.deepEqual(fullCalls, [['A'], null], 'Reconnect/full-list refresh takes precedence');

let failFirst = true;
const failures: RefreshScope[] = [];
const recovering = createRefreshQueue<'tournaments'>(async (_key, scope) => {
  failures.push(scope);
  if (failFirst) { failFirst = false; throw new Error('Temporary outage'); }
});
const failure = recovering('tournaments', ['A']);
const recovery = recovering('tournaments', ['B']);
await assert.rejects(failure);
await recovery;
assert.deepEqual(failures, [['A'], ['B']], 'A failed read does not block the next saved update');
console.log('Refresh queue checks passed: scoped merges, serialization, independent resources, reconnect and recovery.');
