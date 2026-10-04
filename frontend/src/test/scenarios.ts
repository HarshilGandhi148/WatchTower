// TEST-only synthetic display data. Never imported by the application entrypoint.
import type {
  Advisory,
  DashboardData,
  Projection,
} from '../features/live/types';

export function scenarioData(now = Date.now()): DashboardData {
  const at = (minutes: number) =>
    new Date(now + minutes * 60_000).toISOString();
  const projection: Projection = {
    id: 'test-projection-1',
    snapshotId: 'test-snapshot-1',
    snapshotAtUtc: at(-1),
    modelVersion: 'Demo aggregate flow v1',
    pressureUnit: 'demand/capacity ratio',
    baseline: [0, 5, 10, 15].map((minute, i) => ({
      minute,
      runwayQueue: [8, 12, 15, 17][i],
      upstreamWaiting: [3, 2, 3, 4][i],
      totalWaitingAircraftMinutes: [0, 52, 125, 218][i],
      departures: [0, 8, 16, 24][i],
      pressure: [1, 1.2, 1.4, 1.5][i],
    })),
    proposed: [0, 5, 10, 15].map((minute, i) => ({
      minute,
      runwayQueue: [8, 10, 10, 11][i],
      upstreamWaiting: [3, 4, 8, 10][i],
      totalWaitingAircraftMinutes: [0, 55, 133, 231][i],
      departures: [0, 8, 16, 24][i],
      pressure: [1, 1.1, 1.15, 1.2][i],
    })),
    assumptions: [
      'Sample model; no live provider input.',
      'Both scenarios use identical demand and capacity assumptions.',
    ],
    limitations: [
      'Reduced modeled runway queue increases upstream and total waiting.',
      'Readiness is an assumption; nearby aircraft are not a verified departure queue.',
    ],
    aircraftPaths: {
      basis: 'illustrative',
      assumptions: [
        'Hand-authored MHacks demo paths, separate from the aggregate flow calculation.',
        'Waypoints illustrate scenario playback; they are not flight plans, clearances, or predicted taxi routes.',
      ],
      trajectories: [
        {
          aircraftId: 'test-plane-1',
          label: 'DEMO101',
          baseline: [
            [0, 42.226, -83.366],
            [5, 42.27, -83.32],
            [10, 42.34, -83.27],
            [15, 42.42, -83.21],
          ],
          proposed: [
            [0, 42.226, -83.366],
            [5, 42.25, -83.34],
            [10, 42.29, -83.306],
            [15, 42.36, -83.254],
          ],
        },
        {
          aircraftId: 'test-plane-2',
          label: 'DEMO202',
          baseline: [
            [0, 42.205, -83.334],
            [5, 42.16, -83.4],
            [10, 42.09, -83.49],
            [15, 42.02, -83.57],
          ],
          proposed: [
            [0, 42.205, -83.334],
            [5, 42.19, -83.355],
            [10, 42.16, -83.4],
            [15, 42.12, -83.452],
          ],
        },
        {
          aircraftId: 'test-plane-3',
          label: 'DEMO303',
          baseline: [
            [0, 42.252, -83.38],
            [5, 42.29, -83.46],
            [10, 42.32, -83.55],
            [15, 42.36, -83.65],
          ],
          proposed: [
            [0, 42.252, -83.38],
            [5, 42.264, -83.405],
            [10, 42.285, -83.45],
            [15, 42.31, -83.52],
          ],
        },
      ].map((path) => ({
        ...path,
        baseline: path.baseline.map(([minute, latitudeDeg, longitudeDeg]) => ({
          minute,
          latitudeDeg,
          longitudeDeg,
        })),
        proposed: path.proposed.map(([minute, latitudeDeg, longitudeDeg]) => ({
          minute,
          latitudeDeg,
          longitudeDeg,
        })),
      })),
    },
  };
  const base: Advisory = {
    id: 'test-advisory-1',
    version: 1,
    snapshotId: projection.snapshotId,
    title: 'Review departure release window',
    response: 'Review the aggregate release rate for the next 15 minutes.',
    reason:
      'The comparison shifts waiting from the runway queue to upstream. Total waiting remains higher in the proposed scenario.',
    priority: 'attention',
    status: 'pending_review',
    ownerRole: 'Flow coordinator',
    resource: 'DTW departure flow',
    windowStartUtc: at(0),
    windowEndUtc: at(15),
    createdAtUtc: at(-1),
    expiresAtUtc: at(10),
    quality: 'limited',
    evidence: [
      {
        id: 'test-evidence-1',
        source: 'Sample demand scenario',
        atUtc: at(-1),
        summary:
          'Simulated demand for this comparison; live schedules are unavailable.',
      },
    ],
    assumptions: [
      'Modeled capacity: 2 aircraft/min.',
      'Readiness is modeled, not observed.',
    ],
    limitations: [
      'Not an aircraft clearance or an operational recommendation.',
    ],
    projectionId: projection.id,
    parameters: [
      {
        key: 'releaseRate',
        label: 'Aggregate release rate',
        value: 2,
        min: 1,
        max: 3,
        step: 0.5,
        unit: 'aircraft/min',
      },
    ],
  };
  return {
    refreshedAtUtc: at(0),
    session: null,
    sources: [
      {
        id: 'weather',
        name: 'Aviation weather',
        status: 'fresh',
        sourceAtUtc: at(-20),
        fetchedAtUtc: at(-0.3),
        detail: 'Sample report; observation and retrieval times differ.',
        attribution: 'Sample weather data · live source not connected',
      },
      {
        id: 'positions',
        name: 'Nearby positions',
        status: 'fresh',
        sourceAtUtc: at(-0.1),
        fetchedAtUtc: at(-0.05),
        detail:
          'Sample positions; airport association is specified per aircraft.',
        attribution: 'Sample aircraft data · live source not connected',
      },
      {
        id: 'faa',
        name: 'FAA events',
        status: 'limited',
        sourceAtUtc: at(-3),
        fetchedAtUtc: at(-1),
        detail: 'Sample published-event coverage is limited.',
        attribution: 'Sample event data · live source not connected',
      },
      {
        id: 'demand',
        name: 'Flight demand',
        status: 'unknown',
        sourceAtUtc: null,
        fetchedAtUtc: null,
        detail:
          'Live schedules are not connected. Projection values are simulated.',
        attribution: '',
      },
    ],
    aircraft: [
      {
        id: 'test-plane-1',
        label: 'DEMO101',
        latitudeDeg: 42.226,
        longitudeDeg: -83.366,
        trackDeg: 29,
        observedAtUtc: at(-0.1),
        dtwAssociated: false,
      },
      {
        id: 'test-plane-2',
        label: 'DEMO202',
        latitudeDeg: 42.205,
        longitudeDeg: -83.334,
        trackDeg: 209,
        observedAtUtc: at(-0.2),
        dtwAssociated: true,
      },
      {
        id: 'test-plane-3',
        label: 'DEMO303',
        latitudeDeg: 42.252,
        longitudeDeg: -83.38,
        trackDeg: null,
        observedAtUtc: at(-0.1),
        dtwAssociated: false,
      },
    ],
    nearbyCount: 3,
    weather: {
      summary: 'Clear conditions',
      basis: 'Observed',
      atUtc: at(-20),
      facts: [
        { label: 'Wind', value: '240° / 8 kt' },
        { label: 'Visibility', value: '10 mi' },
        { label: 'Ceiling', value: 'Unknown' },
      ],
    },
    eventsStatus: 'none_published',
    events: [],
    findings: ['Weather', 'Congestion', 'Validation'].map((agent, index) => ({
      agent,
      summary: [
        'Sample weather: clear conditions; no weather constraint modeled.',
        'Sample flow: proposed release timing shifts waiting upstream.',
        'Sample validation: review the increase in total waiting before deciding.',
      ][index],
      atUtc: at(-1),
    })),
    advisories: [
      base,
      {
        ...base,
        id: 'test-advisory-2',
        title: 'Monitor nearby traffic pressure',
        priority: 'information',
        response: 'Monitor the nearby traffic-density trend.',
        reason:
          'Aircraft positions support density analysis. Departure demand is unverified.',
        projectionId: 'test-density',
        parameters: [],
      },
      {
        ...base,
        id: 'test-advisory-3',
        title: 'Review modeled departure pressure',
        priority: 'time_sensitive',
        response:
          'Compare baseline and proposed pressure before accepting the window.',
        parameters: [],
      },
      {
        ...base,
        id: 'test-advisory-4',
        title: 'Review source coverage limitations',
        priority: 'information',
        projectionId: null,
        parameters: [],
      },
    ],
    projections: [
      projection,
      {
        ...projection,
        id: 'test-density',
        baseline: projection.baseline.map((p) => ({
          ...p,
          runwayQueue: null,
          upstreamWaiting: null,
          totalWaitingAircraftMinutes: null,
          departures: null,
        })),
        proposed: projection.proposed.map((p) => ({
          ...p,
          runwayQueue: null,
          upstreamWaiting: null,
          totalWaitingAircraftMinutes: null,
          departures: null,
        })),
        limitations: [
          'Departure metrics are unavailable without verified live demand.',
        ],
      },
    ],
    history: [],
  };
}
