import { Timestamp } from 'spacetimedb';
import { describe, expect, it } from 'vitest';
import { aircraftFreshness, displayTime, metarFreshness } from './time';
const timestamp = (millis: number) => new Timestamp(BigInt(millis) * 1000n);

describe('freshness and timezone boundaries', () => {
  it('uses aircraft-specific limits and keeps missing age unknown', () => {
    const at = timestamp(0);
    expect(aircraftFreshness(at, undefined, 0)).toBe('unknown');
    for (const [seconds, status] of [
      [30, 'fresh'],
      [31, 'aging'],
      [60, 'aging'],
      [61, 'stale'],
      [120, 'stale'],
      [121, 'excluded'],
    ] as const)
      expect(aircraftFreshness(at, 0, seconds * 1000)).toBe(status);
    expect(aircraftFreshness(at, 61, 0)).toBe('stale');
  });
  it('keeps METAR current through 90 minutes and limits older reports', () => {
    expect(metarFreshness(timestamp(0), 90 * 60_000)).toBe('current');
    expect(metarFreshness(timestamp(0), 90 * 60_000 + 1)).toBe('limited');
  });
  it('displays Detroit DST and falls back to UTC for invalid timezones', () => {
    const before = Timestamp.fromDate(new Date('2026-11-01T05:30:00Z'));
    const after = Timestamp.fromDate(new Date('2026-11-01T06:30:00Z'));
    expect(displayTime(before, 'America/Detroit')).toContain('EDT');
    expect(displayTime(after, 'America/Detroit')).toContain('EST');
    expect(displayTime(before, 'invalid/timezone')).toContain('(UTC)');
  });
});
