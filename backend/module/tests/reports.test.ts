import { describe, expect, it, vi } from 'vitest';
import { Timestamp } from 'spacetimedb';
vi.mock('spacetimedb/server', () => ({ t: { string: () => ({}) } }));
vi.mock('../src/schema', () => ({
  database: { reducer: (_: unknown, body: unknown) => body },
}));
import { store_validation_report } from '../src/reducers/reports';
const at = '2026-10-04T12:00:00.000Z';
const expires = '2026-10-04T12:01:00.000Z';
const stamp = (v: string) => new Timestamp(BigInt(Date.parse(v)) * 1000n);
const report = {
  schema_version: 1,
  report_id: 'TEST-report',
  candidate_id: 'TEST-candidate',
  snapshot_id: 'TEST-snapshot',
  case_id: 'TEST-case',
  assessment_round: 1,
  created_at_utc: at,
  expires_at_utc: expires,
  outcome: 'supported',
  suggestion: 'Review this flow window.',
  limitations: ['TEST only'],
  checks: [
    'binding',
    'freshness',
    'metrics',
    'inputs',
    'action',
    'projection',
  ].map((name) => ({
    name,
    status: name === 'projection' ? 'not_applicable' : 'pass',
    reason: 'TEST check',
    evidence_ids: ['TEST-evidence'],
  })),
};
function fixture(role = 'validation', now = at) {
  const saved = new Map<
    string,
    { reportId: string; candidateId: string; contentJson: string }
  >();
  return {
    saved,
    ctx: {
      sender: 'TEST',
      timestamp: stamp(now),
      db: {
        servicePermissions: { identity: { find: () => ({ role }) } },
        validationReports: {
          reportId: { find: (id: string) => saved.get(id) },
          candidateId: {
            find: (id: string) =>
              [...saved.values()].find((r) => r.candidateId === id),
          },
          insert: (r: {
            reportId: string;
            candidateId: string;
            contentJson: string;
          }) => saved.set(r.reportId, r),
        },
        flowCandidates: {
          candidateId: {
            find: (id: string) =>
              id === 'TEST-candidate'
                ? {
                    snapshotId: 'TEST-snapshot',
                    caseRound: 'TEST-case:1',
                    expiresAtUtc: stamp(expires),
                    contentJson: JSON.stringify({
                      envelope: {
                        created_at_utc: at,
                        evidence_ids: ['TEST-evidence'],
                      },
                      projection_id: null,
                    }),
                  }
                : undefined,
          },
        },
        evidenceSnapshots: {
          snapshotId: {
            find: (id: string) =>
              id === 'TEST-snapshot'
                ? {
                    snapshotId: 'TEST-snapshot',
                    caseId: 'TEST-case',
                    expiresAtUtc: stamp(expires),
                    closed: false,
                  }
                : undefined,
          },
        },
        evidenceRecords: {
          evidenceId: {
            find: (id: string) => (id === 'TEST-evidence' ? {} : undefined),
          },
        },
      },
    },
  };
}
function save(ctx: unknown, v: unknown = report) {
  (
    store_validation_report as unknown as (
      ctx: unknown,
      args: { payloadJson: string },
    ) => void
  )(ctx, { payloadJson: JSON.stringify(v) });
}
describe('validation report persistence', () => {
  it('allows only validation and makes identical retries idempotent', () => {
    for (const role of [
      'ingestion',
      'weather',
      'congestion',
      'admin',
      'unauthorized',
    ])
      expect(() => save(fixture(role).ctx)).toThrow();
    const { ctx, saved } = fixture();
    save(ctx);
    save(ctx);
    expect(saved.size).toBe(1);
    expect(() => save(ctx, { ...report, suggestion: 'conflicting' })).toThrow();
  });
  it.each([
    { snapshot_id: 'missing' },
    { candidate_id: 'missing' },
    { assessment_round: 2 },
    { created_at_utc: 'yesterday' },
    { expires_at_utc: '2026-10-04T11:00:00Z' },
    { outcome: 'approved' },
    { checks: [] },
  ])('rejects malformed/mismatched reports %j', (change) =>
    expect(() => save(fixture().ctx, { ...report, ...change })).toThrow(),
  );
  it('rejects expired submission and a supported report with failed checks', () => {
    expect(() => save(fixture('validation', expires).ctx)).toThrow();
    expect(() =>
      save(fixture().ctx, {
        ...report,
        checks: report.checks.map((c) => ({ ...c, status: 'fail' })),
      }),
    ).toThrow();
  });
  it('rejects evidence outside the candidate and duplicate check names', () => {
    expect(() =>
      save(fixture().ctx, {
        ...report,
        checks: report.checks.map((c) => ({ ...c, evidence_ids: ['missing'] })),
      }),
    ).toThrow();
    expect(() =>
      save(fixture().ctx, {
        ...report,
        checks: report.checks.map((c) => ({
          ...c,
          name: 'binding',
          status: 'pass',
        })),
      }),
    ).toThrow();
  });
});
