import { table, t } from 'spacetimedb/server';

// JSON payloads are versioned shared contracts, validated by reducers.
export const feedBatches = table(
  { name: 'feed_batches', public: true },
  {
    batchId: t.string().primaryKey(),
    provider: t.string(),
    fetchedAtUtc: t.timestamp(),
    sourceAtUtc: t.option(t.timestamp()),
    payloadJson: t.string(),
  },
);
export const latestFeeds = table(
  { name: 'latest_feeds', public: true },
  {
    provider: t.string().primaryKey(),
    batchId: t.string(),
  },
);
export const airportEvents = table(
  { name: 'airport_events', public: true },
  {
    eventId: t.string().primaryKey(),
    batchId: t.string(),
    airportIcao: t.string(),
    contentJson: t.string(),
  },
);
export const evidenceRecords = table(
  { name: 'evidence_records', public: true },
  {
    evidenceId: t.string().primaryKey(),
    provider: t.string(),
    contentJson: t.string(),
  },
);
export const evidenceSnapshots = table(
  { name: 'evidence_snapshots', public: true },
  {
    snapshotId: t.string().primaryKey(),
    caseId: t.string().unique(),
    airportIcao: t.string(),
    cutoffAtUtc: t.timestamp(),
    expiresAtUtc: t.timestamp(),
    requestJson: t.string(),
    contentJson: t.string(),
    closed: t.bool().index(),
  },
);
export const agentFindings = table(
  { name: 'agent_findings', public: true },
  {
    findingId: t.string().primaryKey(),
    snapshotId: t.string(),
    caseRound: t.string().unique(),
    expiresAtUtc: t.timestamp(),
    contentJson: t.string(),
  },
);
export const flowCandidates = table(
  { name: 'flow_candidates', public: true },
  {
    candidateId: t.string().primaryKey(),
    snapshotId: t.string(),
    caseRound: t.string().unique(),
    expiresAtUtc: t.timestamp(),
    contentJson: t.string(),
  },
);
export const pendingDeliveries = table(
  { name: 'pending_deliveries', public: true },
  {
    candidateId: t.string().primaryKey(),
    expiresAtUtc: t.timestamp(),
    contentJson: t.string(),
    status: t.string().index(),
    updatedAtUtc: t.timestamp(),
  },
);
export const projectionResults = table(
  { name: 'projection_results', public: true },
  {
    projectionId: t.string().primaryKey(),
    snapshotId: t.string(),
    contentJson: t.string(),
  },
);
export const assessmentRequests = table(
  { name: 'assessment_requests', public: true },
  {
    caseRound: t.string().primaryKey(),
    snapshotId: t.string(),
    contentJson: t.string(),
  },
);
export const flightRecords = table(
  { name: 'flight_records', public: true },
  {
    batchId: t.string().primaryKey(),
    fetchedAtUtc: t.timestamp(),
    contentJson: t.string(),
  },
);
