import type { Timestamp } from 'spacetimedb';

export function aircraftFreshness(
  observed: Timestamp,
  positionAgeS: number | undefined,
  now: number,
): 'fresh' | 'aging' | 'stale' | 'excluded' | 'unknown' {
  if (positionAgeS === undefined) return 'unknown';
  const age =
    Math.max(0, (now - Number(observed.toMillis())) / 1000) + positionAgeS;
  if (age > 120) return 'excluded';
  if (age > 60) return 'stale';
  return age <= 30 ? 'fresh' : 'aging';
}

export function metarFreshness(
  observed: Timestamp,
  now: number,
): 'current' | 'limited' {
  return now - Number(observed.toMillis()) > 90 * 60_000
    ? 'limited'
    : 'current';
}

export function displayTime(timestamp: Timestamp, timezone: string): string {
  try {
    return new Intl.DateTimeFormat('en-US', {
      timeZone: timezone,
      dateStyle: 'short',
      timeStyle: 'long',
    }).format(timestamp.toDate());
  } catch {
    return `${timestamp.toISOString()} (UTC)`;
  }
}
