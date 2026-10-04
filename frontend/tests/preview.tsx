import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { MissionControl } from '../src/components/exchange/MissionControl';
import { createDashboardClient, emptyData } from '../src/lib/connection';
import { exchangeData, sanitize } from '../src/features/exchange/adapter';
import type { DashboardSnapshot } from '../src/features/live/types';
import type { EvidenceDetail } from '../src/features/exchange/types';
import captures from './replay-captures.json';
import { scenarioData } from '../src/test/scenarios';
import type { Projection } from '../src/features/live/types';
import '../src/styles.css';

const client = createDashboardClient(undefined, 'TEST');
const names = [
  'Weather + traffic concern',
  'Correction round',
  'Provider failure',
];
const parsed = captures.map((c) => {
  const exchange = exchangeData({
    ...c.rows,
    deliveries: c.rows.deliveries.map((d) => ({
      ...d,
      updatedAtUtc: { toISOString: () => new Date(d.atUtc).toISOString() },
    })),
  });
  return {
    ...c,
    exchange: {
      ...exchange,
      activity: exchange.activity.map((a) => ({
        ...a,
        provenance: 'TEST' as const,
      })),
    },
  };
});
const evidenceLoader = async (id: string): Promise<EvidenceDetail> => {
  const s = parsed.find((c) => c.snapshot.snapshot_id === id)?.snapshot;
  if (!s) throw new Error('Missing TEST evidence');
  return {
    snapshotId: id,
    windowStartUtc: s.window_start_utc,
    windowEndUtc: s.window_end_utc,
    evidenceIds: s.evidence_ids,
    content: sanitize(s),
  };
};
export default function TestPreview() {
  const [scenario, setScenario] = useState(0),
    [elapsed, setElapsed] = useState(0),
    [playing, setPlaying] = useState(true);
  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(
      () => setElapsed((t) => Math.min(45, t + 0.5)),
      250,
    );
    return () => clearInterval(timer);
  }, [playing]);
  const c = parsed[scenario];
  const count = Math.min(
    c.exchange.activity.length,
    Math.floor(elapsed / (45 / c.exchange.activity.length)),
  );
  const activity = c.exchange.activity.slice(0, count);
  const reports = c.exchange.reports.filter((r) =>
    activity.some((a) => a.id === r.report_id),
  );
  const now = Date.parse(c.capturedAtUtc) + elapsed * 1000;
  const original = scenarioData(Date.parse(c.capturedAtUtc)).projections[0];
  const projection: Projection = {
    ...original,
    id: `TEST-paths-${c.id}`,
    snapshotId: c.snapshot.snapshot_id,
    modelVersion: 'TEST route illustration',
    baseline: [],
    proposed: [],
    aircraftPaths: {
      basis: 'illustrative',
      assumptions: [
        'Original hand-authored TEST routes, translated to the selected snapshot positions. No operational route model.',
      ],
      trajectories: (original.aircraftPaths?.trajectories ?? []).flatMap(
        (route, i) => {
          const plane = c.aircraft[i];
          if (!plane) return [];
          const origin = route.baseline[0];
          const shift = (point: typeof origin) => ({
            ...point,
            latitudeDeg:
              point.latitudeDeg + plane.latitudeDeg - origin.latitudeDeg,
            longitudeDeg:
              point.longitudeDeg + plane.longitudeDeg - origin.longitudeDeg,
          });
          return [
            {
              ...route,
              aircraftId: plane.id,
              label: plane.label,
              baseline: route.baseline.map(shift),
              proposed: route.proposed.map(shift),
            },
          ];
        },
      ),
    },
  };
  const snapshot: DashboardSnapshot = {
    ...emptyData,
    mode: 'TEST',
    connection: 'ready',
    connectionMessage: 'TEST replay',
    aircraft: c.aircraft,
    nearbyCount: scenario === 2 ? null : c.aircraft.length,
    weather: {
      summary: 'TEST METAR · gusty conditions',
      basis: 'Observed',
      atUtc: c.capturedAtUtc,
      facts: [
        { label: 'Wind', value: '18 kt · gusts 27 kt' },
        { label: 'Visibility', value: '8,000 m' },
      ],
    },
    sources: [
      {
        id: 'test-adsb',
        name: 'ADSB.lol / TEST',
        status: scenario === 2 ? 'unavailable' : 'fresh',
        sourceAtUtc: c.capturedAtUtc,
        fetchedAtUtc: c.capturedAtUtc,
        detail:
          scenario === 2
            ? 'TEST provider failure'
            : 'Synthetic TEST aircraft batch',
        attribution: 'TEST fixture',
      },
      {
        id: 'test-awc',
        name: 'AWC / TEST',
        status: 'limited',
        sourceAtUtc: c.capturedAtUtc,
        fetchedAtUtc: c.capturedAtUtc,
        detail: 'Weather test double with synthetic input',
        attribution: 'TEST fixture',
      },
    ],
    exchange: { activity, reports, issues: c.exchange.issues },
  };
  return (
    <MissionControl
      key={scenario}
      client={client}
      evidenceLoader={evidenceLoader}
      replay={{
        snapshot,
        now,
        projection,
        controls: (
          <div className="replay-controls">
            <select
              aria-label="Replay scenario"
              value={scenario}
              onChange={(e) => {
                setScenario(Number(e.target.value));
                setElapsed(0);
                setPlaying(true);
              }}
            >
              {names.map((n, i) => (
                <option key={n} value={i}>
                  {n}
                </option>
              ))}
            </select>
            <button
              onClick={() => {
                if (elapsed >= 45) setElapsed(0);
                setPlaying(!playing);
              }}
            >
              {playing && elapsed < 45 ? 'Pause' : 'Play'}
            </button>
            <button
              onClick={() => {
                setElapsed(0);
                setPlaying(true);
              }}
            >
              Restart
            </button>
            <progress aria-label="Replay progress" max={45} value={elapsed} />
            <small>{Math.floor(elapsed)} / 45 s</small>
          </div>
        ),
      }}
    />
  );
}
const root = document.getElementById('root');
if (root) createRoot(root).render(<TestPreview />);
