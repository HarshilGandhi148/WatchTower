import { object, text, time } from './adapter';
import type { RecommendationReview, ValidationReport } from './types';

export function reviewFromJson(raw: unknown): RecommendationReview {
  const v = object(raw);
  if (
    v.schema_version !== 1 ||
    !['approve', 'disapprove'].includes(text(v.decision)) ||
    ![1, 2].includes(Number(v.assessment_round))
  )
    throw new Error('Invalid review');
  return {
    report_id: text(v.report_id),
    snapshot_id: text(v.snapshot_id),
    assessment_round: Number(v.assessment_round),
    decision: v.decision as RecommendationReview['decision'],
    reviewed_at_utc: time(v.reviewed_at_utc),
  };
}

export async function saveRecommendationReview(
  report: ValidationReport,
  decision: RecommendationReview['decision'],
  requestId: string,
): Promise<void> {
  const response = await fetch('/api/recommendation-reviews', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      schema_version: 1,
      report_id: report.report_id,
      snapshot_id: report.snapshot_id,
      assessment_round: report.assessment_round,
      decision,
      request_id: requestId,
    }),
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    throw new Error(
      typeof result.error === 'string'
        ? result.error
        : 'Your review has not been saved. Please retry.',
    );
  }
}
