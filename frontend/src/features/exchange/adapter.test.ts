import { describe, expect, it } from 'vitest';
import captures from '../../../tests/replay-captures.json';
import { exchangeData, reportFromJson, sanitize } from './adapter';
const captured = captures[0];
describe('stored exchange adapter', () => {
  it('retains case evidence, corrects rounds, and deduplicates retry updates', () => {
    const data = exchangeData(captured.rows);
    expect(data.issues).toEqual([]);
    expect(data.activity).toHaveLength(5);
    const replay = exchangeData({
      ...captured.rows,
      findings: [...captured.rows.findings, ...captured.rows.findings],
    });
    expect(replay.activity).toHaveLength(5);
    expect(
      data.activity.every(
        (a) => a.snapshotId === captured.snapshot.snapshot_id,
      ),
    ).toBe(true);
    expect(data.reports[0].outcome).toBe('supported');
    expect(exchangeData(captures[1].rows).activity.at(-1)?.round).toBe(2);
  });
  it('keeps receipt distinct from validation and reports missing/oversized inputs', () => {
    const data = exchangeData({ ...captured.rows, reports: [] });
    expect(data.activity.some((a) => a.event === 'Received by validator')).toBe(
      true,
    );
    expect(data.reports).toEqual([]);
    expect(
      exchangeData({ ...captured.rows, findings: [{ contentJson: 'invalid' }] })
        .issues,
    ).toHaveLength(1);
    expect(
      exchangeData({
        ...captured.rows,
        findings: Array(1001).fill({ contentJson: '{}' }),
      }).issues[0],
    ).toContain('too many');
  });
  it('rejects malformed reports and removes secrets from JSON evidence', () => {
    expect(() => reportFromJson({ schema_version: 1 })).toThrow();
    expect(
      sanitize({
        token: 'secret',
        source: 'https://example.test/path?key=hidden',
        nested: { headers: { Authorization: 'secret' }, value: 12 },
      }),
    ).toEqual({ source: 'https://example.test/path', nested: { value: 12 } });
  });
});
