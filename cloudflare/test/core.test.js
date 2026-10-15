import test from 'node:test';
import assert from 'node:assert/strict';
import {buildReport, stats, summaries, exportParts, csv, period, nextRun} from '../src/core.js';
import {activityTargets} from '../src/providers.js';
import {people, schema, options, bounds} from './helpers.js';

const report = buildReport([...people, people[0]], schema, options, bounds, 'Test');
test('cohorts use creation bounds, require UTMs, deduplicate persons, and match exact tags', () => {
  assert.deepEqual(report.rows.map(r => r.person_id), ['1', '2', '3']);
  assert.equal(report.rows[1].matches_tag, false);
  assert.equal(report.rows[0].phones[0], '+12125550100');
  const total = stats(report.rows, options);
  assert.equal(total.qualified_pct, 66.67); assert.equal(total.contacted_pct, 33.33);
  assert.equal(summaries(report)[0].qualified_pct, 50);
});
test('CSV detail filter does not change denominators; selected groups never export other people', () => {
  const group = report.rows[0].group_key;
  const parts = exportParts(report, [group], true);
  assert.match(parts['summary.csv'], /"2"/);
  assert.match(parts['people.csv'], /"50"/);
  assert.ok(!parts['people.csv'].includes('Unqualified lead'));
  assert.ok(!parts['people.csv'].includes('Other source'));
  assert.ok(exportParts(report)['people.csv'].includes('Other source'));
  assert.ok(!exportParts(report, ['["NO_LEADS"]'])['people.csv'].includes('Mark'));
});
test('unconfigured stats are blank, not zero; CSV defangs formulas and quotes text', () => {
  const s = stats(report.rows, {...options, stat_tags: {qualified: [], contacted: []}});
  assert.equal(s.qualified_pct, null);
  assert.match(csv([{name: '=SUM(A1:A3)', phone: '+12125550100', text: 'a,"b"'}]), /'=SUM/);
  assert.ok(csv([{name: 'a,"b"'}]).includes('a,""b""'));
});
test('date presets use local calendar days across DST and include custom end date', () => {
  const spring = period({preset: 'Yesterday', timezone: 'America/New_York'}, new Date('2026-03-09T16:00:00Z'));
  assert.equal(Date.parse(spring.end) - Date.parse(spring.start), 23 * 3600000);
  const fall = period({preset: 'Yesterday', timezone: 'America/New_York'}, new Date('2026-11-02T16:00:00Z'));
  assert.equal(Date.parse(fall.end) - Date.parse(fall.start), 25 * 3600000);
  const custom = period({preset: 'Custom', timezone: 'America/New_York', start_date: '2026-10-01', end_date: '2026-10-01'}, new Date('2026-10-10T12:00:00Z'));
  assert.equal(custom.end, '2026-10-02T04:00:00.000Z');
});
test('schedules handle month ends, DST gaps, repeated hours and elapsed-hour intervals', () => {
  assert.equal(nextRun({frequency: 'monthly', time: '09:00', day: 31, timezone: 'America/New_York'}, new Date('2026-02-01T00:00:00Z')), '2026-02-28T14:00:00.000Z');
  assert.equal(nextRun({frequency: 'daily', time: '02:30', timezone: 'America/New_York'}, new Date('2026-03-08T06:00:00Z')), '2026-03-08T07:30:00.000Z');
  assert.equal(nextRun({frequency: 'daily', time: '01:30', timezone: 'America/New_York'}, new Date('2026-11-01T05:45:00Z')), '2026-11-02T06:30:00.000Z');
  assert.equal(nextRun({frequency: 'interval', hours: 24, anchor: '2026-03-07T14:00:00Z'}, new Date('2026-03-07T15:00:00Z')), '2026-03-08T14:00:00.000Z');
});
test('shared phone numbers are not silently attributed to two people', () => {
  const cloned = structuredClone(report); cloned.rows[1].phones = cloned.rows[0].phones;
  const target = activityTargets(cloned).find(t => t.id === '1');
  assert.equal(target.shared, true); assert.deepEqual(target.phones, []);
});
