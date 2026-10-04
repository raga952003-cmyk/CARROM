import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { GroupStageSettings } from '../../src/components/admin/GroupStageSettings';
import { knockoutProgression } from '../../src/components/admin/QualifierCountInput';

const html = renderToStaticMarkup(React.createElement(GroupStageSettings, {
  format: 'league_knockout', groupCount: 1, qualifiersPerGroup: 2,
  knockoutQualifiers: 20, expectedEntrants: 40,
  onGroupCountChange: () => {}, onQualifiersChange: () => {},
  onKnockoutQualifiersChange: () => {}, onExpectedEntrantsChange: () => {},
}));
assert.match(html, /20 enter the bracket; 19 knockout matches/);
assert.match(html, /Preliminary: 4 matches; top 12 seeds receive a bye/);
assert.match(html, /Round of 16/);
assert.match(html, /Quarterfinals/);
assert.match(html, /aria-label="Number of league qualifiers"/);
assert.equal(knockoutProgression(8), 'Quarterfinals (8) → Semifinals (4) → Final (2)');
console.log('Qualifier form and progression checks passed');
