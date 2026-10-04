import { describe, expect, it } from 'vitest';
import { ageLabel, formatTime, isExpired } from './time';
describe('Detroit time display', () => {
  it('distinguishes both occurrences of the fall-back hour', () => {
    expect(formatTime('2026-11-01T05:30:00Z')).toContain('01:30:00 AM EDT');
    expect(formatTime('2026-11-01T06:30:00Z')).toContain('01:30:00 AM EST');
    expect(formatTime('2026-11-01T05:30:00Z', 'America/Detroit', 'clock')).toBe(
      '01:30 AM EDT',
    );
    expect(formatTime('2026-11-01T06:30:00Z', 'America/Detroit', 'clock')).toBe(
      '01:30 AM EST',
    );
  });
  it('skips the spring-forward gap', () => {
    expect(formatTime('2026-03-08T06:59:00Z')).toContain('01:59:00 AM EST');
    expect(formatTime('2026-03-08T07:00:00Z')).toContain('03:00:00 AM EDT');
  });
  it('treats missing and invalid time as unknown and invalid expiry as expired', () => {
    expect(formatTime(null)).toBe('Unknown');
    expect(ageLabel('invalid', Date.now())).toBe('age unknown');
    expect(isExpired('invalid')).toBe(true);
    expect(formatTime('2026-10-03T12:00:00Z', 'invalid')).toContain('UTC');
  });
});
