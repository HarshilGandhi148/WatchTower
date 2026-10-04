import { useRef, useState } from 'react';
import { saveRecommendationReview } from '../../features/exchange/reviews';
import type {
  RecommendationReview,
  ValidationReport,
} from '../../features/exchange/types';

export function RecommendationReviewControls({
  report,
  review,
  enabled,
  save = saveRecommendationReview,
  onReviewed,
}: {
  report: ValidationReport;
  review?: RecommendationReview;
  enabled: boolean;
  save?: typeof saveRecommendationReview;
  onReviewed?: (reportId: string) => void;
}) {
  const [pending, setPending] = useState(false);
  const [saved, setSaved] = useState<RecommendationReview['decision']>();
  const [error, setError] = useState('');
  const requests = useRef<
    Partial<Record<RecommendationReview['decision'], string>>
  >({});
  const decision = review?.decision ?? saved;
  const approveAllowed =
    report.outcome !== 'rejected' &&
    !report.checks.some(
      (c) =>
        ['binding', 'freshness', 'action', 'projection'].includes(c.name) &&
        c.status === 'fail',
    );
  const submit = async (value: RecommendationReview['decision']) => {
    if (
      pending ||
      decision ||
      !enabled ||
      (value === 'approve' && !approveAllowed)
    )
      return;
    setPending(true);
    setError('');
    const requestId = (requests.current[value] ??= crypto.randomUUID());
    try {
      await save(report, value, requestId);
      setSaved(value);
      onReviewed?.(report.report_id);
    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : 'Your review has not been saved. Please retry.',
      );
    } finally {
      setPending(false);
    }
  };
  return (
    <div className="recommendation-review" aria-label="Recommendation review">
      <div className="review-buttons">
        <button
          className="review-approve"
          disabled={!enabled || pending || !!decision || !approveAllowed}
          onClick={() => void submit('approve')}
        >
          ✓ Approve
        </button>
        <button
          className="review-disapprove"
          disabled={!enabled || pending || !!decision}
          onClick={() => void submit('disapprove')}
        >
          × Disapprove
        </button>
      </div>
      <span role="status">
        {pending
          ? 'Saving review…'
          : decision
            ? `${decision === 'approve' ? 'Approved' : 'Disapproved'} · Saved`
            : ''}
      </span>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
