export type AgentRole = 'weather' | 'congestion' | 'validation';
export interface AgentActivity {
  id: string;
  caseId: string;
  snapshotId: string;
  round: number;
  agent: AgentRole;
  event: string;
  atUtc: string;
  summary: string;
  evidenceIds: string[];
  windowStartUtc?: string;
  windowEndUtc?: string;
  provenance: 'LIVE' | 'TEST';
}
export interface ValidationReport {
  report_id: string;
  candidate_id: string;
  snapshot_id: string;
  case_id: string;
  assessment_round: number;
  created_at_utc: string;
  expires_at_utc: string;
  outcome: 'supported' | 'insufficient_data' | 'rejected';
  suggestion: string;
  limitations: string[];
  checks: {
    name: string;
    status: 'pass' | 'fail' | 'not_applicable';
    reason: string;
    evidence_ids: string[];
  }[];
}
export interface ExchangeData {
  activity: AgentActivity[];
  reports: ValidationReport[];
  issues: string[];
}
export interface EvidenceDetail {
  snapshotId: string;
  windowStartUtc: string;
  windowEndUtc: string;
  evidenceIds: string[];
  content: unknown;
}
