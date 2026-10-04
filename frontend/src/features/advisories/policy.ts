import type {
  Advisory,
  DashboardSnapshot,
  EditableParameter,
} from '../live/types';
import { isExpired } from '../../lib/time';

export function decisionBlockReason(
  state: DashboardSnapshot,
  advisory: Advisory,
  now: number,
) {
  if (state.connection !== 'ready')
    return 'Decisions are disabled until the connection is synchronized.';
  if (!state.session) return 'A controller session is required.';
  if (!state.session.decidableAdvisoryIds.includes(advisory.id))
    return 'Read-only: this advisory is assigned to another controller.';
  if (advisory.status !== 'pending_review')
    return `This version is ${advisory.status.replaceAll('_', ' ')}.`;
  if (isExpired(advisory.expiresAtUtc, now))
    return 'This advisory has expired. Wait for a validated version.';
  return null;
}
export function activeAdvisories(advisories: readonly Advisory[], now: number) {
  const latest = new Map<string, Advisory>();
  for (const advisory of advisories) {
    if ((latest.get(advisory.id)?.version ?? 0) < advisory.version)
      latest.set(advisory.id, advisory);
  }
  const rank = { time_sensitive: 0, attention: 1, information: 2 };
  return [...latest.values()]
    .filter(
      (a) => a.status === 'pending_review' && !isExpired(a.expiresAtUtc, now),
    )
    .sort(
      (a, b) =>
        rank[a.priority] - rank[b.priority] ||
        Date.parse(a.windowStartUtc) - Date.parse(b.windowStartUtc) ||
        Date.parse(a.createdAtUtc) - Date.parse(b.createdAtUtc),
    );
}
export function validParameter(parameter: EditableParameter, value: number) {
  const steps = (value - parameter.min) / parameter.step;
  return (
    Number.isFinite(value) &&
    parameter.step > 0 &&
    value >= parameter.min &&
    value <= parameter.max &&
    Math.abs(steps - Math.round(steps)) < 1e-7
  );
}
