import type { AgentActivity, ExchangeData, ValidationReport } from './types';
type Row = {
  contentJson: string;
  status?: string;
  updatedAtUtc?: { toISOString(): string };
};
type ObjectValue = Record<string, unknown>;
export function object(v: unknown): ObjectValue {
  if (!v || typeof v !== 'object' || Array.isArray(v))
    throw new Error('Invalid stored record');
  return v as ObjectValue;
}
export function text(v: unknown): string {
  if (typeof v !== 'string') throw new Error('Invalid text');
  return v;
}
export function time(v: unknown): string {
  const value = text(v);
  if (!Number.isFinite(Date.parse(value)) || !value.endsWith('Z'))
    throw new Error('Invalid UTC time');
  return value;
}
export function strings(v: unknown): string[] {
  if (!Array.isArray(v)) throw new Error('Invalid references');
  return v.map(text);
}
export function reportFromJson(raw: unknown): ValidationReport {
  const v = object(raw);
  if (
    v.schema_version !== 1 ||
    !['supported', 'insufficient_data', 'rejected'].includes(text(v.outcome)) ||
    ![1, 2].includes(Number(v.assessment_round))
  )
    throw new Error('Invalid report');
  if (!Array.isArray(v.checks) || v.checks.length !== 6)
    throw new Error('Missing checks');
  return {
    report_id: text(v.report_id),
    candidate_id: text(v.candidate_id),
    snapshot_id: text(v.snapshot_id),
    case_id: text(v.case_id),
    assessment_round: Number(v.assessment_round),
    created_at_utc: time(v.created_at_utc),
    expires_at_utc: time(v.expires_at_utc),
    outcome: v.outcome as ValidationReport['outcome'],
    suggestion: text(v.suggestion),
    limitations: strings(v.limitations),
    checks: v.checks.map((raw) => {
      const c = object(raw);
      if (!['pass', 'fail', 'not_applicable'].includes(text(c.status)))
        throw new Error('Invalid check');
      return {
        name: text(c.name),
        status: c.status as ValidationReport['checks'][number]['status'],
        reason: text(c.reason),
        evidence_ids: strings(c.evidence_ids),
      };
    }),
  };
}
export function exchangeData(groups: {
  requests: Row[];
  findings: Row[];
  candidates: Row[];
  deliveries: Row[];
  reports: Row[];
}): ExchangeData {
  const activity: AgentActivity[] = [],
    reports: ValidationReport[] = [],
    issues: string[] = [];
  for (const [kind, rows] of Object.entries(groups)) {
    if (rows.length > 1000) {
      issues.push(
        `${kind}: too many records; narrow the database retention window.`,
      );
      continue;
    }
    for (const row of rows)
      try {
        const v = object(JSON.parse(row.contentJson));
        if (kind === 'reports') {
          const r = reportFromJson(v);
          reports.push(r);
          activity.push({
            id: r.report_id,
            caseId: r.case_id,
            snapshotId: r.snapshot_id,
            round: r.assessment_round,
            agent: 'validation',
            event: 'Evidence checked',
            atUtc: r.created_at_utc,
            summary: r.suggestion,
            evidenceIds: [...new Set(r.checks.flatMap((c) => c.evidence_ids))],
            provenance: 'LIVE',
          });
          continue;
        }
        const e = object(v.envelope);
        if (e.schema_version !== 1) throw new Error('Unsupported message');
        const a: AgentActivity = {
          id: `${kind}:${text(e.message_id)}`,
          caseId: text(e.case_id),
          snapshotId: text(e.snapshot_id),
          round: Number(v.assessment_round ?? 1),
          agent: 'congestion',
          event: '',
          atUtc: time(e.created_at_utc),
          summary: '',
          evidenceIds: strings(e.evidence_ids),
          provenance: 'LIVE',
        };
        if (kind === 'requests') {
          a.agent = 'validation';
          a.event =
            a.round === 2 ? 'Reassessment requested' : 'Assessment requested';
          a.summary =
            'Assess traffic and weather against the same frozen evidence snapshot.';
        } else if (kind === 'findings') {
          a.agent = 'weather';
          a.event = 'Weather assessment stored';
          a.summary = text(v.summary);
          a.windowStartUtc = time(v.window_start_utc);
          a.windowEndUtc = time(v.window_end_utc);
        } else if (kind === 'candidates') {
          a.event = 'Candidate saved';
          const metrics = Array.isArray(v.traffic_metrics)
            ? v.traffic_metrics.map(object)
            : [];
          const count = metrics.find((m) => m.name === 'nearby_aircraft_total');
          a.summary = `${count && typeof count.value === 'number' ? `${count.value} nearby aircraft observed.` : 'Current nearby traffic is unknown.'} ${strings(v.required_missing_inputs).length ? `Limitations: ${strings(v.required_missing_inputs).join('; ')}.` : 'Candidate prepared for evidence checks.'}`;
          a.windowStartUtc = time(v.window_start_utc);
          a.windowEndUtc = time(v.window_end_utc);
          // Candidate rows retain their assessment round in the delivery record.
          const delivery = groups.deliveries.find((d) => {
            try {
              return (
                object(JSON.parse(d.contentJson)).candidate_id === e.message_id
              );
            } catch {
              return false;
            }
          });
          if (delivery)
            a.round = Number(
              object(JSON.parse(delivery.contentJson)).assessment_round,
            );
        } else {
          if (row.status !== 'received') continue;
          a.agent = 'validation';
          a.id += ':received';
          a.event = 'Received by validator';
          a.summary =
            'Saved candidate retrieved. Receipt confirms delivery; evidence checks are separate.';
          a.atUtc = row.updatedAtUtc?.toISOString() ?? a.atUtc;
        }
        activity.push(a);
      } catch {
        issues.push(`${kind}: a malformed record could not be displayed.`);
      }
  }
  return {
    activity: [...new Map(activity.map((a) => [a.id, a])).values()].sort(
      (a, b) => a.atUtc.localeCompare(b.atUtc) || a.id.localeCompare(b.id),
    ),
    reports: reports.sort((a, b) =>
      a.created_at_utc.localeCompare(b.created_at_utc),
    ),
    issues: [...new Set(issues)],
  };
}
// Evidence is plain data. Strip credential-like fields and URL queries from the optional JSON view.
export function sanitize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sanitize);
  if (value && typeof value === 'object')
    return Object.fromEntries(
      Object.entries(value)
        .filter(
          ([key]) =>
            !/token|secret|authorization|api.?key|headers|seed/i.test(key),
        )
        .map(([key, v]) => [key, sanitize(v)]),
    );
  if (typeof value === 'string' && /^https?:\/\//.test(value)) {
    try {
      const u = new URL(value);
      return `${u.origin}${u.pathname}`;
    } catch {
      return '[invalid URL]';
    }
  }
  return value;
}

export function evidenceFacts(
  value: unknown,
): { label: string; value: string }[] {
  const facts: { label: string; value: string }[] = [];
  try {
    const root = object(value);
    for (const raw of Array.isArray(root.batches) ? root.batches : []) {
      const batch = object(raw),
        payload = object(batch.payload),
        snapshot = object(payload.snapshot ?? payload);
      facts.push({
        label: text(payload.provider),
        value: `Fetched ${text(snapshot.fetched_at_utc ?? payload.fetched_at_utc)}`,
      });
      if (snapshot.provider_time_utc)
        facts.push({
          label: 'Source timestamp',
          value: text(snapshot.provider_time_utc),
        });
      if (Array.isArray(snapshot.observations))
        facts.push({
          label: 'Aircraft in copied batch',
          value: String(snapshot.observations.length),
        });
      if (Array.isArray(snapshot.events))
        facts.push({
          label: 'Published events in batch',
          value: String(snapshot.events.length),
        });
    }
    for (const raw of Array.isArray(root.weather) ? root.weather : []) {
      const row = object(raw);
      for (const [key, unit] of [
        ['wind_speed_kt', 'kt'],
        ['wind_gust_kt', 'kt'],
        ['visibility_m', 'm'],
        ['ceiling_ft', 'ft'],
      ])
        if (typeof row[key] === 'number')
          facts.push({
            label: key.replaceAll('_', ' '),
            value: `${row[key]} ${unit}`,
          });
    }
  } catch {
    facts.push({
      label: 'Details',
      value: 'Some source fields could not be decoded.',
    });
  }
  return facts;
}
export function evidenceAircraft(
  value: unknown,
  references: readonly string[],
): string[] {
  try {
    const root = object(value);
    return (Array.isArray(root.batches) ? root.batches : []).flatMap((raw) => {
      const batch = object(raw);
      if (!references.includes(text(batch.batch_id))) return [];
      const payload = object(batch.payload);
      if (payload.provider !== 'adsb_lol') return [];
      const snapshot = object(payload.snapshot);
      return (
        Array.isArray(snapshot.observations) ? snapshot.observations : []
      ).map((raw) => text(object(raw).aircraft_id));
    });
  } catch {
    return [];
  }
}
