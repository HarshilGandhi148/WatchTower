import { table, t } from 'spacetimedb/server';

export const sourceHealth = table(
  { name: 'source_health', public: true },
  {
    provider: t.string().primaryKey(),
    fetchedAtUtc: t.timestamp(),
    sourceAtUtc: t.option(t.timestamp()),
    lastSuccessAtUtc: t.option(t.timestamp()),
    status: t.string(),
    failureCount: t.u32(),
  },
);

export const weatherRecords = table(
  { name: 'weather_records', public: true },
  {
    provider: t.string().primaryKey(),
    airportIcao: t.string(),
    recordType: t.string(),
    observedAtUtc: t.timestamp(),
    fetchedAtUtc: t.timestamp(),
    sourceRef: t.string(),
    windDirectionDeg: t.option(t.f64()),
    windSpeedKt: t.option(t.f64()),
    windGustKt: t.option(t.f64()),
    visibilityM: t.option(t.f64()),
    ceilingFt: t.option(t.f64()),
    contentJson: t.string(),
  },
);

export const aircraftObservations = table(
  { name: 'aircraft_observations', public: true },
  {
    aircraftId: t.string().primaryKey(),
    provider: t.string(),
    observedAtUtc: t.timestamp(),
    fetchedAtUtc: t.timestamp(),
    sourceRef: t.string(),
    latitudeDeg: t.option(t.f64()),
    longitudeDeg: t.option(t.f64()),
    altitudeFt: t.option(t.f64()),
    speedKt: t.option(t.f64()),
    trackDeg: t.option(t.f64()),
    positionAgeS: t.option(t.f64()),
    onGround: t.option(t.bool()),
    contentJson: t.string(),
  },
);
