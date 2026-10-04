import { t } from 'spacetimedb/server';
import { database } from '../schema';
import { requireRole } from '../auth/roles';
import { canonicalJson, parse, round } from './evidence_rules';
import { object, text } from './validation';

// The private loopback demo gateway acts as the designated review controller.
// This records a human review of informational advice, never an aircraft action.
export const review_recommendation = database.reducer(
  { payloadJson: t.string() },
  (ctx, { payloadJson }) => {
    requireRole(
      ctx.db.servicePermissions.identity.find(ctx.sender)?.role,
      'admin',
    );
    const v = object(parse(payloadJson), [
      'schema_version',
      'report_id',
      'snapshot_id',
      'assessment_round',
      'decision',
      'request_id',
    ]);
    const reportId = text(v.report_id),
      requestId = text(v.request_id);
    if (
      v.schema_version !== 1 ||
      !['approve', 'disapprove'].includes(text(v.decision)) ||
      !/^[a-f0-9-]{36}$/.test(requestId)
    )
      throw new Error('Invalid review');
    const prior = ctx.db.recommendationReviews.reportId.find(reportId);
    if (prior) {
      const stored = parse(prior.contentJson);
      if (
        stored.request_id !== requestId ||
        stored.decision !== v.decision ||
        stored.snapshot_id !== v.snapshot_id ||
        stored.assessment_round !== v.assessment_round
      )
        throw new Error('Recommendation already reviewed');
      return;
    }
    const report = ctx.db.validationReports.reportId.find(reportId);
    const now = Number(ctx.timestamp.toMillis());
    if (
      !report ||
      report.snapshotId !== text(v.snapshot_id) ||
      Number(report.createdAtUtc.toMillis()) > now ||
      Number(report.expiresAtUtc.toMillis()) <= now
    )
      throw new Error('Recommendation expired or changed');
    const result = parse(report.contentJson);
    if (result.assessment_round !== round(v.assessment_round))
      throw new Error('Wrong assessment round');
    for (const other of ctx.db.validationReports.iter()) {
      const next = parse(other.contentJson);
      if (
        next.case_id === result.case_id &&
        Number(next.assessment_round) > Number(result.assessment_round)
      )
        throw new Error('Recommendation superseded');
    }
    if (
      v.decision === 'approve' &&
      (result.outcome === 'rejected' ||
        (result.checks as Record<string, unknown>[]).some(
          (c) =>
            ['binding', 'freshness', 'action', 'projection'].includes(
              String(c.name),
            ) && c.status === 'fail',
        ))
    )
      throw new Error('Recommendation requires reassessment');
    ctx.db.recommendationReviews.insert({
      reportId,
      requestId,
      reviewerIdentity: ctx.sender,
      reviewedAtUtc: ctx.timestamp,
      contentJson: canonicalJson({
        ...v,
        reviewed_at_utc: new Date(now).toISOString(),
      }),
    });
  },
);
