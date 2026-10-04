import {
  agentIdentity,
  ingestEvent,
  weatherReading,
  congestionReading,
  validationResult,
} from './tables/streams';
import { schema } from 'spacetimedb/server';

import {
  validationReports,
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
import { recommendationReviews } from './tables/reviews';
import {
  aircraftObservations,
  sourceHealth,
  weatherRecords,
} from './tables/live';

export const database = schema({
  agentIdentity,
  ingestEvent,
  weatherReading,
  congestionReading,
  validationResult,
  servicePermissions,
  validationReports,
  recommendationReviews,
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
