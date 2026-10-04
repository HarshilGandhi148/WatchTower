// Frontend display models, not database rows or generated wire contracts.
// The integration adapter maps reviewed SDK bindings into these models.
export type FeedStatus =
  'fresh' | 'limited' | 'stale' | 'unavailable' | 'unknown';
export interface SourceHealth {
  id: string;
  name: string;
  status: FeedStatus;
  sourceAtUtc: string | null;
  fetchedAtUtc: string | null;
  detail: string;
  attribution: string;
  attributionUrl?: string;
}
export interface Aircraft {
  id: string;
  label: string;
  latitudeDeg: number;
  longitudeDeg: number;
  trackDeg: number | null;
  observedAtUtc: string;
  dtwAssociated: boolean;
}
export interface WeatherDisplay {
  summary: string;
  basis: 'Observed' | 'Forecast' | 'Conditional';
  atUtc: string;
  facts: readonly { label: string; value: string }[];
}
export interface EvidenceDisplay {
  id: string;
  source: string;
  atUtc: string;
  summary: string;
}
export interface EditableParameter {
  key: string;
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit: string;
}
export type AdvisoryStatus =
  | 'pending_review'
  | 'approved'
  | 'ignored'
  | 'expired'
  | 'superseded'
  | 'revalidating';
export interface Advisory {
  id: string;
  version: number;
  snapshotId: string;
  title: string;
  response: string;
  reason: string;
  priority: 'information' | 'attention' | 'time_sensitive';
  status: AdvisoryStatus;
  ownerRole: string;
  resource: string;
  windowStartUtc: string;
  windowEndUtc: string;
  createdAtUtc: string;
  expiresAtUtc: string;
  quality: 'adequate' | 'limited' | 'unavailable';
  evidence: readonly EvidenceDisplay[];
  assumptions: readonly string[];
  limitations: readonly string[];
  projectionId: string | null;
  parameters: readonly EditableParameter[];
}
export interface FlowPoint {
  minute: number;
  runwayQueue: number | null;
  upstreamWaiting: number | null;
  totalWaitingAircraftMinutes: number | null;
  departures: number | null;
  pressure: number | null;
}
export interface Projection {
  id: string;
  snapshotId: string;
  snapshotAtUtc: string;
  modelVersion: string;
  pressureUnit: string;
  baseline: readonly FlowPoint[];
  proposed: readonly FlowPoint[];
  assumptions: readonly string[];
  limitations: readonly string[];
  aircraftPaths?: {
    basis: 'illustrative' | 'modeled';
    trajectories: readonly AircraftTrajectory[];
    assumptions: readonly string[];
  };
}
export interface AircraftPathPoint {
  minute: number;
  latitudeDeg: number;
  longitudeDeg: number;
}
export interface AircraftTrajectory {
  aircraftId: string;
  label: string;
  baseline: readonly AircraftPathPoint[];
  proposed: readonly AircraftPathPoint[];
}
export interface HistoryEntry {
  id: string;
  advisoryId: string;
  version: number;
  title: string;
  status: Exclude<AdvisoryStatus, 'pending_review' | 'revalidating'> | 'edited';
  actor: string;
  atUtc: string;
  snapshotId: string;
  projectionId: string | null;
  parameters: readonly { label: string; value: number; unit: string }[];
}
export interface ControllerSession {
  identity: string;
  displayName: string;
  // Determined by the server-side session/assignment adapter, never a role picker.
  decidableAdvisoryIds: readonly string[];
}
export interface DashboardData {
  refreshedAtUtc: string | null;
  sources: readonly SourceHealth[];
  aircraft: readonly Aircraft[];
  nearbyCount: number | null;
  weather: WeatherDisplay | null;
  eventsStatus: 'unavailable' | 'none_published' | 'available';
  events: readonly {
    id: string;
    title: string;
    detail: string;
    atUtc: string;
  }[];
  findings: readonly { agent: string; summary: string; atUtc: string }[];
  advisories: readonly Advisory[];
  projections: readonly Projection[];
  history: readonly HistoryEntry[];
  session: ControllerSession | null;
}
export interface DashboardSnapshot extends DashboardData {
  mode: 'LIVE' | 'TEST';
  connection:
    | 'unconfigured'
    | 'connecting'
    | 'synchronizing'
    | 'ready'
    | 'disconnected'
    | 'error';
  connectionMessage: string;
}
export type ControllerCommand =
  | {
      kind: 'decision';
      requestId: string;
      advisoryId: string;
      version: number;
      snapshotId: string;
      decision: 'approve' | 'ignore';
    }
  | {
      kind: 'edit';
      requestId: string;
      advisoryId: string;
      version: number;
      snapshotId: string;
      parameters: Readonly<Record<string, number>>;
      rationale: string;
    };
export interface CommandAcknowledgement {
  requestId: string;
  message: string;
}
