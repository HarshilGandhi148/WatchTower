import { Timestamp } from 'spacetimedb';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type {
  AircraftObservations,
  SourceHealth,
  WeatherRecords,
} from '../module_bindings/types';
import { createDashboardClient } from './connection';
import {
  createSpacetimeTransport,
  observationData,
} from './spacetimeTransport';

const sdk = vi.hoisted(() => ({ builder: undefined as unknown }));
vi.mock('./connection', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./connection')>()),
  createConnectionBuilder: () => sdk.builder,
}));
const now = Date.parse('2026-10-04T12:00:00Z');
const timestamp = (ms = now) => Timestamp.fromDate(new Date(ms));
const plane: AircraftObservations = {
  aircraftId: 'test-plane',
  provider: 'adsb_lol',
  observedAtUtc: timestamp(),
  fetchedAtUtc: timestamp(),
  sourceRef: 'test',
  latitudeDeg: 42.2124,
  longitudeDeg: -83.3534,
  altitudeFt: undefined,
  speedKt: undefined,
  trackDeg: undefined,
  positionAgeS: 10,
  onGround: undefined,
  contentJson: '{}',
};
const metar: WeatherRecords = {
  provider: 'awc',
  airportIcao: 'KDTW',
  recordType: 'METAR',
  observedAtUtc: timestamp(),
  fetchedAtUtc: timestamp(),
  sourceRef: 'test',
  windDirectionDeg: undefined,
  windSpeedKt: undefined,
  windGustKt: undefined,
  visibilityM: 0,
  ceilingFt: undefined,
  contentJson: '{}',
};
const health: SourceHealth = {
  provider: 'adsb_lol',
  fetchedAtUtc: timestamp(),
  sourceAtUtc: timestamp(),
  lastSuccessAtUtc: timestamp(),
  status: 'ok',
  failureCount: 0,
};
afterEach(() => {
  vi.useRealTimers();
  sdk.builder = undefined;
});

function harness() {
  const attempts: ReturnType<typeof attempt>[] = [];
  function attempt() {
    let onConnect: (db: ReturnType<typeof database>) => void = () => {};
    let onDisconnect: () => void = () => {};
    let onError: () => void = () => {};
    let applied: () => void = () => {};
    let subscriptionError: () => void = () => {};
    let update: () => void = () => {};
    const weather: WeatherRecords[] = [];
    function table<T>(rows: T[]) {
      return {
        iter: () => rows.values(),
        onInsert: (cb: () => void) => {
          update = cb;
        },
        onUpdate: (cb: () => void) => {
          update = cb;
        },
        onDelete: (cb: () => void) => {
          update = cb;
        },
      };
    }
    const subscription = {
      onApplied(cb: () => void) {
        applied = cb;
        return subscription;
      },
      onError(cb: () => void) {
        subscriptionError = cb;
        return subscription;
      },
      subscribe: vi.fn(),
    };
    function database() {
      return {
        db: {
          weatherRecords: table(weather),
          aircraftObservations: table([plane]),
          sourceHealth: table([health]),
          assessmentRequests: table([]),
          agentFindings: table([]),
          flowCandidates: table([]),
          pendingDeliveries: table([]),
          validationReports: table([]),
        },
        subscriptionBuilder: () => subscription,
        disconnect: vi.fn(() => onDisconnect()),
      };
    }
    const db = database();
    const builder = {
      onConnect(cb: typeof onConnect) {
        onConnect = cb;
        return builder;
      },
      onDisconnect(cb: () => void) {
        onDisconnect = cb;
        return builder;
      },
      onConnectError(cb: () => void) {
        onError = cb;
        return builder;
      },
      build() {
        const next = attempt();
        attempts.push(next);
        sdk.builder = next.builder;
        return db;
      },
    };
    return {
      builder,
      db,
      weather,
      connected: () => onConnect(db),
      applied: () => applied(),
      update: () => update(),
      disconnected: () => onDisconnect(),
      error: () => onError(),
      subscriptionError: () => subscriptionError(),
      subscription,
    };
  }
  const first = attempt();
  attempts.push(first);
  sdk.builder = first.builder;
  return attempts;
}

describe('observation dashboard mapping', () => {
  it('keeps missing data unknown and unsupported controller features unavailable', () => {
    const data = observationData([], [], [], now);
    expect(data.nearbyCount).toBeNull();
    expect(data.weather).toBeNull();
    expect(data.sources.map((source) => source.status)).toEqual([
      'unknown',
      'unknown',
    ]);
    expect(data.session).toBeNull();
    expect(data.advisories).toEqual([]);
  });
  it('preserves null measurements, zero values, units and position age', () => {
    const data = observationData([metar], [plane], [health], now);
    expect(data.weather?.facts.map((fact) => fact.value)).toEqual([
      'Unknown',
      '0 m',
      'Unknown',
    ]);
    expect(data.aircraft[0].observedAtUtc).toBe(
      new Date(now - 10_000).toISOString(),
    );
    expect(data.aircraft[0].trackDeg).toBeNull();
    expect(data.aircraft[0].dtwAssociated).toBe(false);
    expect(data.nearbyCount).toBe(1);
  });
  it('excludes missing/expired positions and limits stale METAR without inferring zero', () => {
    expect(
      observationData(
        [],
        [{ ...plane, positionAgeS: undefined }],
        [health],
        now,
      ).aircraft,
    ).toEqual([]);
    const data = observationData([metar], [plane], [health], now + 91 * 60_000);
    expect(data.aircraft).toEqual([]);
    expect(data.nearbyCount).toBeNull();
    expect(data.weather?.summary).toContain('limited');
  });
  it('retains observations on provider failure while marking coverage unavailable', () => {
    const data = observationData(
      [],
      [plane],
      [{ ...health, status: 'failed' }],
      now,
    );
    expect(data.aircraft).toHaveLength(1);
    expect(data.nearbyCount).toBeNull();
    expect(data.sources[1].status).toBe('unavailable');
  });
});

describe('SDK dashboard lifecycle', () => {
  it('does not connect with missing configuration', () => {
    expect(createSpacetimeTransport()).toBeUndefined();
  });
  it('waits for subscriptions, replaces updates, ages data and cleans up', () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const attempts = harness();
    const client = createDashboardClient(createSpacetimeTransport());
    const stop = client.start();
    const first = attempts[0];
    first.connected();
    expect(client.getSnapshot().connection).toBe('synchronizing');
    expect(first.subscription.subscribe).toHaveBeenCalledOnce();
    first.applied();
    expect(client.getSnapshot().connection).toBe('ready');
    first.weather.push(metar);
    first.update();
    expect(client.getSnapshot().weather?.basis).toBe('Observed');
    first.weather.splice(0);
    first.update();
    expect(client.getSnapshot().weather).toBeNull();
    vi.advanceTimersByTime(121_000);
    expect(client.getSnapshot().aircraft).toEqual([]);
    stop();
    const snapshot = client.getSnapshot();
    first.applied();
    first.update();
    vi.advanceTimersByTime(30_000);
    expect(client.getSnapshot()).toBe(snapshot);
    expect(vi.getTimerCount()).toBe(0);
  });
  it('reconnects after disconnect and ignores obsolete callbacks until resynchronized', () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const attempts = harness();
    const client = createDashboardClient(createSpacetimeTransport());
    const stop = client.start();
    const first = attempts[0];
    first.connected();
    first.applied();
    first.disconnected();
    expect(client.getSnapshot().connection).toBe('disconnected');
    first.update();
    expect(client.getSnapshot().connection).toBe('disconnected');
    vi.advanceTimersByTime(1000);
    expect(client.getSnapshot().connection).toBe('synchronizing');
    const second = attempts[1];
    second.connected();
    first.applied();
    expect(client.getSnapshot().connection).toBe('synchronizing');
    second.applied();
    expect(client.getSnapshot().connection).toBe('ready');
    expect(client.getSnapshot().session).toBeNull();
    stop();
  });
  it.each(['error', 'subscriptionError'] as const)(
    'reports safe %s and retries',
    (failure) => {
      vi.useFakeTimers();
      vi.setSystemTime(now);
      const attempts = harness();
      const client = createDashboardClient(createSpacetimeTransport());
      const stop = client.start();
      attempts[0].connected();
      attempts[0][failure]();
      expect(client.getSnapshot().connection).toBe('error');
      vi.advanceTimersByTime(1000);
      expect(client.getSnapshot().connection).toBe('synchronizing');
      stop();
    },
  );
});
