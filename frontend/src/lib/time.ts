export function formatTime(
  value: string | null,
  timezone = 'America/Detroit',
  variant: 'full' | 'clock' = 'full',
) {
  if (!value || !Number.isFinite(Date.parse(value))) return 'Unknown';
  try {
    return new Intl.DateTimeFormat('en-US', {
      month: variant === 'full' ? 'short' : undefined,
      day: variant === 'full' ? 'numeric' : undefined,
      hour: '2-digit',
      minute: '2-digit',
      second: variant === 'full' ? '2-digit' : undefined,
      timeZone: timezone,
      timeZoneName: 'short',
    }).format(new Date(value));
  } catch {
    return new Date(value).toISOString() + ' UTC';
  }
}
export function ageLabel(value: string | null, now: number) {
  if (!value || !Number.isFinite(Date.parse(value))) return 'age unknown';
  const seconds = Math.max(0, Math.floor((now - Date.parse(value)) / 1000));
  return seconds < 60 ? `${seconds}s ago` : `${Math.floor(seconds / 60)}m ago`;
}
export function isExpired(value: string, now = Date.now()) {
  const time = Date.parse(value);
  return !Number.isFinite(time) || time <= now;
}
