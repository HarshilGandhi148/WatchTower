import { schema } from 'spacetimedb/server';

import { servicePermissions } from './auth/permissions';
import {
  aircraftObservations,
  sourceHealth,
  weatherRecords,
} from './tables/live';

export const database = schema({
  servicePermissions,
  aircraftObservations,
  sourceHealth,
  weatherRecords,
});
