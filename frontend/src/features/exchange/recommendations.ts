import type { ValidationReport } from './types';

export const recommendationWindowMs = 10 * 60_000;
export const recommendationLimit = 5;

/** Current results only; corrections replace the previous round of their case. */
export function currentRecommendations(
  reports: ValidationReport[],
  now: number,
): ValidationReport[] {
  const latest = new Map<string, ValidationReport>();
  for (const report of reports) {
    const created = Date.parse(report.created_at_utc);
    const expires = Date.parse(report.expires_at_utc);
    if (!Number.isFinite(created) || !Number.isFinite(expires) || created > now)
      continue;
    const previous = latest.get(report.case_id);
    if (
      !previous ||
      report.assessment_round > previous.assessment_round ||
      (report.assessment_round === previous.assessment_round &&
        created > Date.parse(previous.created_at_utc))
    )
      latest.set(report.case_id, report);
  }
  return [...latest.values()]
    .filter(
      (r) =>
        Date.parse(r.expires_at_utc) > now &&
        Date.parse(r.created_at_utc) > now - recommendationWindowMs,
    )
    .sort(
      (a, b) =>
        Date.parse(b.created_at_utc) - Date.parse(a.created_at_utc) ||
        b.report_id.localeCompare(a.report_id),
    )
    .slice(0, recommendationLimit);
}
