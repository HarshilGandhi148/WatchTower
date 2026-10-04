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
  details?: string[];
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
  recommendation_kind?: 'demo' | 'monitoring';
  aircraft_recommendations?: AircraftRecommendation[];
}
export interface AircraftRecommendation {
  aircraft_id: string;
  label: string;
  recommendation: string;
  prediction: string;
  evidence_id: string;
  observed_at_utc: string;
  forecast_at_utc: string | null;
  projected_latitude_deg: number | null;
  projected_longitude_deg: number | null;
}
export interface ExchangeData {
  activity: AgentActivity[];
  reports: ValidationReport[];
  issues: string[];
  reviews?: RecommendationReview[];
}
export interface RecommendationReview {
  report_id: string;
  snapshot_id: string;
  assessment_round: number;
  decision: 'approve' | 'disapprove';
  reviewed_at_utc: string;
}
export interface EvidenceDetail {
  snapshotId: string;
  windowStartUtc: string;
  windowEndUtc: string;
  evidenceIds: string[];
  content: unknown;
}
