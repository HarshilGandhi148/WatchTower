import { describe, expect, it } from 'vitest';
import { currentRecommendations } from './recommendations';
import type { ValidationReport } from './types';

const now = Date.parse('2026-10-04T12:00:00Z');
function report(id: string, age = 0, lifetime = 60_000): ValidationReport {
  return {
    report_id: id,
    case_id: id,
    candidate_id: id,
    snapshot_id: id,
    assessment_round: 1,
    created_at_utc: new Date(now - age).toISOString(),
    expires_at_utc: new Date(now + lifetime).toISOString(),
    outcome: 'supported',
    suggestion: 'TEST assessment',
    limitations: [],
    checks: [],
  };
}
describe('rolling recommendations', () => {
  it('removes expired and aged results on the clock, without needing an update', () => {
    const rows = [
      report('expired', 1000, 0),
      report('aged', 300_000),
      report('current'),
    ];
    expect(currentRecommendations(rows, now).map((r) => r.report_id)).toEqual([
      'current',
    ]);
    expect(currentRecommendations(rows, now + 60_000)).toEqual([]);
  });
  it('keeps five newest cases and ignores invalid or future timestamps', () => {
    const rows = Array.from({ length: 8 }, (_, i) =>
      report(String(i), i * 1000),
    );
    rows.push(report('future', -1000), {
      ...report('invalid'),
      expires_at_utc: 'bad',
    });
    expect(
      currentRecommendations(rows.reverse(), now).map((r) => r.report_id),
    ).toEqual(['0', '1', '2', '3', '4']);
  });
  it('supersedes the old round even when its replacement has expired', () => {
    const first = report('first', 10_000);
    const corrected = {
      ...report('corrected', 5000),
      case_id: first.case_id,
      assessment_round: 2,
    };
    expect(currentRecommendations([corrected, first], now)).toEqual([
      corrected,
    ]);
    expect(
      currentRecommendations(
        [first, { ...corrected, expires_at_utc: new Date(now).toISOString() }],
        now,
      ),
    ).toEqual([]);
  });
});
