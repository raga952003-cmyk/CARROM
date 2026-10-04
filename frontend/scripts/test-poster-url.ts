import assert from 'node:assert/strict';
import { DEFAULT_PUBLIC_SITE_URL, isLocalPosterUrl, publicPosterUrl, validatePosterBaseUrl } from '../src/utils/posterFacts';

assert.equal(DEFAULT_PUBLIC_SITE_URL, 'https://carrom-umber-six.vercel.app/');
assert.equal(publicPosterUrl('tournament-123'), `${DEFAULT_PUBLIC_SITE_URL}#/poster/tournament-123`);
assert.equal(publicPosterUrl('tournament-123', '  '), `${DEFAULT_PUBLIC_SITE_URL}#/poster/tournament-123`);
assert.equal(publicPosterUrl('player /?#+₹', 'https://carrom-umber-six.vercel.app/events'),
  'https://carrom-umber-six.vercel.app/events/#/poster/player%20%2F%3F%23%2B%E2%82%B9');
assert.equal(validatePosterBaseUrl(' https://CARROM-UMBER-SIX.vercel.app/events/// '),
  'https://carrom-umber-six.vercel.app/events/');
assert.equal(validatePosterBaseUrl('https://carrom-umber-six.vercel.app/'), DEFAULT_PUBLIC_SITE_URL);
assert.equal(validatePosterBaseUrl('https://8.8.8.8/event/'), 'https://8.8.8.8/event/');
assert.equal(isLocalPosterUrl(DEFAULT_PUBLIC_SITE_URL), false);
assert.equal(isLocalPosterUrl('https://8.8.8.8/#/poster/test'), false);

for (const value of [
  '', 'carrom-umber-six.vercel.app', 'http://carrom-umber-six.vercel.app',
  'javascript:alert(1)', 'ftp://carrom-umber-six.vercel.app',
  'https://user:password@carrom-umber-six.vercel.app',
  'https://carrom-umber-six.vercel.app/?token=test', 'https://carrom-umber-six.vercel.app/#/poster/test',
  'https://carrom-umber-six.vercel.app/?', 'https://carrom-umber-six.vercel.app/#',
  'https://localhost', 'https://event.localhost', 'https://event.local', 'https://event.internal',
  'https://0.0.0.0', 'https://127.0.0.1', 'https://127.1', 'https://2130706433',
  'https://10.1.2.3', 'https://172.16.0.1', 'https://172.31.255.255', 'https://192.168.1.1',
  'https://100.64.0.1', 'https://100.127.255.255', 'https://169.254.1.1',
  'https://192.0.0.1', 'https://192.0.2.1', 'https://192.88.99.1',
  'https://198.18.0.1', 'https://198.19.255.255', 'https://198.51.100.1', 'https://203.0.113.1',
  'https://224.0.0.1', 'https://255.255.255.255', 'https://[::1]', 'https://[2606:4700:4700::1111]',
]) assert.throws(() => validatePosterBaseUrl(value), Error, value);

for (const value of ['http://localhost:5173/#/poster/test', 'http://127.0.0.1:5173/',
  'http://192.168.1.5:5173/', 'https://[::1]/', 'not a URL']) {
  assert.equal(isLocalPosterUrl(value), true, value);
}
for (const value of ['https://172.15.255.255', 'https://172.32.0.1',
  'https://100.63.255.255', 'https://100.128.0.1']) {
  assert.equal(isLocalPosterUrl(value), false, value);
}
console.log('Poster URL checks passed: public destination, encoded IDs, subpaths and local/reserved-address rejection.');
