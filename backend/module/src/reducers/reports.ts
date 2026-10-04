import { Timestamp } from 'spacetimedb';
import { t } from 'spacetimedb/server';
import { database } from '../schema';
import { requireRole } from '../auth/roles';
import { canonicalJson, parse, record, strings, round } from './evidence_rules';
import { object, text, time } from './validation';

export const store_validation_report = database.reducer(
  { payloadJson: t.string() },
  (ctx, { payloadJson }) => {
    requireRole(
      ctx.db.servicePermissions.identity.find(ctx.sender)?.role,
      'validation',
    );
    const v = object(parse(payloadJson), [
      'schema_version',
      'report_id',
      'candidate_id',
      'snapshot_id',
      'case_id',
      'assessment_round',
      'created_at_utc',
      'expires_at_utc',
      'outcome',
      'checks',
      'suggestion',
      'limitations',
    ]);
    const reportId = text(v.report_id),
      candidateId = text(v.candidate_id);
    const contentJson = canonicalJson(v);
    const prior = ctx.db.validationReports.reportId.find(reportId);
    if (prior) {
      if (prior.contentJson !== contentJson)
        throw new Error('Conflicting report ID');
      return;
    }
    const c = ctx.db.flowCandidates.candidateId.find(candidateId);
    const s = ctx.db.evidenceSnapshots.snapshotId.find(text(v.snapshot_id));
    const now = Number(ctx.timestamp.toMillis());
    const created = time(v.created_at_utc),
      expires = time(v.expires_at_utc);
    if (
      !c ||
      !s ||
      v.schema_version !== 1 ||
      c.snapshotId !== s.snapshotId ||
      s.caseId !== v.case_id ||
      c.caseRound !== `${s.caseId}:${round(v.assessment_round)}` ||
      expires !== Number(c.expiresAtUtc.toMillis()) ||
      expires <= now ||
      expires > Number(s.expiresAtUtc.toMillis()) ||
      created > now + 60000 ||
      created >= expires ||
      ctx.db.validationReports.candidateId.find(candidateId)
    )
      throw new Error('Invalid report reference or expiry');
    const candidate = parse(c.contentJson);
    if (created < time(record(candidate.envelope).created_at_utc))
      throw new Error('Invalid report timestamp');
    const outcomes = ['supported', 'insufficient_data', 'rejected'];
    if (!outcomes.includes(text(v.outcome)))
      throw new Error('Invalid report outcome');
    text(v.suggestion, 2048);
    strings(v.limitations, 100);
    const names = [
      'binding',
      'freshness',
      'metrics',
      'inputs',
      'action',
      'projection',
    ];
    const allowed = strings(record(candidate.envelope).evidence_ids);
    if (candidate.projection_id) allowed.push(text(candidate.projection_id));
    if (!Array.isArray(v.checks) || v.checks.length !== names.length)
      throw new Error('Missing checks');
    const seen = new Set<string>();
    let failures = 0;
    for (const entry of v.checks) {
      const check = object(entry, ['name', 'status', 'reason', 'evidence_ids']);
      const name = text(check.name);
      if (
        !names.includes(name) ||
        seen.has(name) ||
        !['pass', 'fail', 'not_applicable'].includes(text(check.status)) ||
        (check.status === 'not_applicable' &&
          (name !== 'projection' || candidate.projection_id !== null))
      )
        throw new Error('Invalid check');
      seen.add(name);
      text(check.reason, 2048);
      if (check.status === 'fail') failures++;
      for (const id of strings(check.evidence_ids)) {
        if (
          !allowed.includes(id) ||
          !ctx.db.evidenceRecords.evidenceId.find(id)
        )
          throw new Error('Unresolved report evidence');
      }
    }
    if ((v.outcome === 'supported') !== (failures === 0))
      throw new Error('Contradictory report outcome');
    ctx.db.validationReports.insert({
      reportId,
      candidateId,
      snapshotId: s.snapshotId,
      createdAtUtc: new Timestamp(BigInt(created) * 1000n),
      expiresAtUtc: new Timestamp(BigInt(expires) * 1000n),
      contentJson,
    });
  },
);
