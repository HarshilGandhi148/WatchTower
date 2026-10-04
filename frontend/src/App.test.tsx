import { act, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { Timestamp } from 'spacetimedb';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App';
import { aircraftFreshness, displayTime, metarFreshness } from './lib/time';
import { tables } from './module_bindings';

const state = vi.hoisted(() => ({
  configured: false,
  active: false,
  ready: false,
  error: undefined as Error | undefined,
  weather: [] as unknown[],
  aircraft: [] as unknown[],
  health: [] as unknown[],
}));
vi.mock('./lib/connection', () => ({
  createConnectionBuilder: () => (state.configured ? {} : undefined),
}));
vi.mock('spacetimedb/react', () => ({
  SpacetimeDBProvider: ({ children }: { children: ReactNode }) => children,
  useSpacetimeDB: () => ({
    isActive: state.active,
    connectionError: state.error,
  }),
  useTable: (query: unknown) => [
    query === tables.weatherRecords
      ? state.weather
      : query === tables.aircraftObservations
        ? state.aircraft
        : state.health,
    state.ready,
  ],
}));

beforeEach(() => {
  state.configured = false;
  state.active = false;
  state.ready = false;
  state.error = undefined;
  state.weather = [];
  state.aircraft = [];
  state.health = [];
});
afterEach(() => vi.useRealTimers());
const timestamp = (millis: number) => new Timestamp(BigInt(millis) * 1000n);

function connected() {
  state.configured = state.active = state.ready = true;
}

describe('SpacetimeDB dashboard', () => {
  it('shows unavailable input and exposes no decision controls', () => {
    render(<App />);
    expect(screen.getByRole('status')).toHaveTextContent(
      'No live observations have been received.',
    );
    expect(
      screen.getByRole('heading', { name: 'Connection unavailable' }),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', { name: /approve|edit|ignore/i }),
    ).not.toBeInTheDocument();
  });
  it('distinguishes connecting, subscription loading, and connected without data', () => {
    state.configured = true;
    const { rerender } = render(<App />);
    expect(screen.getByRole('heading', { name: 'Connecting' })).toBeVisible();
    state.active = true;
    rerender(<App />);
    expect(
      screen.getByRole('heading', { name: 'Loading observations' }),
    ).toBeVisible();
    state.ready = true;
    rerender(<App />);
    expect(screen.getByRole('heading', { name: 'Connected' })).toBeVisible();
    expect(screen.getByRole('status')).toHaveTextContent(
      'No live observations',
    );
    expect(screen.getByText(/Current aircraft coverage unknown/)).toBeVisible();
  });
  it('renders subscription updates and preserves unknown measurements', () => {
    connected();
    const { rerender } = render(<App />);
    state.weather = [
      {
        provider: 'awc',
        observedAtUtc: Timestamp.now(),
        windSpeedKt: undefined,
        visibilityM: 0,
        ceilingFt: undefined,
      },
    ];
    rerender(<App />);
    expect(screen.getByRole('status')).toHaveTextContent(
      'Provider observations received.',
    );
    expect(
      screen.getByText(/Wind unknown; visibility 0 m; ceiling unknown/),
    ).toBeVisible();
    state.weather = [{ ...(state.weather[0] as object), windSpeedKt: 12 }];
    rerender(<App />);
    expect(screen.getByText(/Wind 12 kt/)).toBeVisible();
  });
  it('marks disconnect caches stale and waits for subscriptions on reconnect', () => {
    connected();
    state.weather = [{ provider: 'awc', observedAtUtc: Timestamp.now() }];
    const { rerender } = render(<App />);
    state.active = false;
    state.ready = false;
    rerender(<App />);
    expect(screen.getByRole('heading', { name: 'Disconnected' })).toBeVisible();
    expect(
      screen.getByText(/Cached observations below are stale/),
    ).toBeVisible();
    state.active = true;
    rerender(<App />);
    expect(
      screen.getByRole('heading', { name: 'Loading observations' }),
    ).toBeVisible();
    state.ready = true;
    rerender(<App />);
    expect(screen.getByRole('heading', { name: 'Connected' })).toBeVisible();
    expect(
      screen.queryByRole('button', { name: /approve|edit|ignore/i }),
    ).not.toBeInTheDocument();
  });
  it('shows a safe connection error', () => {
    state.configured = true;
    state.error = new Error('sensitive connection detail');
    render(<App />);
    expect(
      screen.getByRole('heading', { name: 'Connection error' }),
    ).toBeVisible();
    expect(screen.queryByText(/sensitive/)).not.toBeInTheDocument();
  });
  it('ages records without another subscription update', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-04T00:00:00.000Z'));
    connected();
    state.aircraft = [
      {
        aircraftId: 'test-plane',
        observedAtUtc: Timestamp.now(),
        positionAgeS: 0,
        latitudeDeg: 42,
        longitudeDeg: -83,
      },
    ];
    render(<App />);
    expect(screen.getByText('test-plane: fresh')).toBeVisible();
    act(() => vi.advanceTimersByTime(31_000));
    expect(screen.getByText('test-plane: aging')).toBeVisible();
    act(() => vi.advanceTimersByTime(30_000));
    expect(screen.getByText('test-plane: stale')).toBeVisible();
    act(() => vi.advanceTimersByTime(60_000));
    expect(screen.getByText('test-plane: excluded')).toBeVisible();
  });
  it('shows provider failures independently from connection readiness', () => {
    connected();
    state.health = [
      {
        provider: 'awc',
        status: 'failed',
        fetchedAtUtc: Timestamp.now(),
        lastSuccessAtUtc: undefined,
      },
    ];
    render(<App />);
    expect(screen.getByRole('heading', { name: 'Connected' })).toBeVisible();
    expect(
      screen.getByText(/awc: fetch failed.*last success unknown/),
    ).toBeVisible();
  });
});

describe('freshness and timezone boundaries', () => {
  it('uses aircraft-specific limits and keeps missing age unknown', () => {
    const at = timestamp(0);
    expect(aircraftFreshness(at, undefined, 0)).toBe('unknown');
    for (const [seconds, status] of [
      [30, 'fresh'],
      [31, 'aging'],
      [60, 'aging'],
      [61, 'stale'],
      [120, 'stale'],
      [121, 'excluded'],
    ] as const)
      expect(aircraftFreshness(at, 0, seconds * 1000)).toBe(status);
    expect(aircraftFreshness(at, 61, 0)).toBe('stale');
  });
  it('keeps METAR current through 90 minutes and limits older reports', () => {
    expect(metarFreshness(timestamp(0), 90 * 60_000)).toBe('current');
    expect(metarFreshness(timestamp(0), 90 * 60_000 + 1)).toBe('limited');
  });
  it('displays Detroit DST and falls back to UTC for invalid timezones', () => {
    const before = Timestamp.fromDate(new Date('2026-11-01T05:30:00Z'));
    const after = Timestamp.fromDate(new Date('2026-11-01T06:30:00Z'));
    expect(displayTime(before, 'America/Detroit')).toContain('EDT');
    expect(displayTime(after, 'America/Detroit')).toContain('EST');
    expect(displayTime(before, 'invalid/timezone')).toContain('(UTC)');
  });
});
