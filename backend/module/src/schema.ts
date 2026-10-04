import { schema } from 'spacetimedb/server';

import {
  flightRecords,
  assessmentRequests,
  feedBatches,
  latestFeeds,
  airportEvents,
  evidenceRecords,
  evidenceSnapshots,
  agentFindings,
  flowCandidates,
  pendingDeliveries,
  projectionResults,
} from './tables/evidence';

import { servicePermissions } from './auth/permissions';
import {
  aircraftObservations,
  sourceHealth,
  weatherRecords,
} from './tables/live';

export const database = schema({
  servicePermissions,
  flightRecords,
  assessmentRequests,
  feedBatches,
  latestFeeds,
  airportEvents,
  evidenceRecords,
  evidenceSnapshots,
  agentFindings,
  flowCandidates,
  pendingDeliveries,
  projectionResults,
  aircraftObservations,
  sourceHealth,
  weatherRecords,
});
