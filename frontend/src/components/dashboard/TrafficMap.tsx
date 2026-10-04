import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import 'maplibre-gl/dist/maplibre-gl.css';
import type { Aircraft } from '../../features/live/types';
import type {
  AircraftMapUpdate,
  MapView,
  TrafficMapController,
} from './mapEngine';
import { MAX_VIEW_RADIUS_NM, MIN_VIEW_RADIUS_NM } from './geography';
import {
  supportedPaths,
  type MapProjection,
} from '../../features/projection/aircraftPaths';

export function TrafficMap({
  aircraft,
  now,
  disconnected,
  timezone,
  mode,
  mapProjection = null,
  children,
}: {
  aircraft: readonly Aircraft[];
  now: number;
  disconnected: boolean;
  timezone: string;
  mode: 'LIVE' | 'TEST';
  mapProjection?: MapProjection | null;
  children?: ReactNode;
}) {
  const container = useRef<HTMLDivElement>(null);
  const credits = useRef<HTMLDivElement>(null);
  const engine = useRef<TrafficMapController | null>(null);
  const latest = useRef<AircraftMapUpdate>({
    aircraft,
    now,
    disconnected,
    timezone,
  });
  const view = useRef<MapView>('airport');
  const radius = useRef(MAX_VIEW_RADIUS_NM);
  const latestProjection = useRef(mapProjection);
  const radiusId = useId();
  const [radiusNm, setRadiusNm] = useState(MAX_VIEW_RADIUS_NM);
  const [selectedView, setSelectedView] = useState<MapView>('airport');
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState<'loading' | 'ready' | 'unavailable'>(
    'loading',
  );
  useEffect(() => {
    latest.current = { aircraft, now, disconnected, timezone };
    engine.current?.updateAircraft(latest.current);
  }, [aircraft, now, disconnected, timezone]);
  useEffect(() => {
    latestProjection.current = mapProjection;
    engine.current?.updateProjection(mapProjection);
  }, [mapProjection]);
  useEffect(() => {
    let disposed = false;
    let controller: TrafficMapController | undefined;
    void import('./mapEngine')
      .then(({ createTrafficMap }) => {
        if (disposed || !container.current || !credits.current) return;
        controller = createTrafficMap(
          container.current,
          {
            ready: () => {
              if (!disposed) setStatus('ready');
            },
            unavailable: () => {
              if (!disposed) setStatus('unavailable');
            },
          },
          credits.current,
        );
        engine.current = controller;
        controller.updateAircraft(latest.current);
        controller.setView(view.current, radius.current);
        controller.updateProjection(latestProjection.current);
      })
      .catch(() => {
        if (!disposed) setStatus('unavailable');
      });
    return () => {
      disposed = true;
      controller?.destroy();
      engine.current = null;
    };
  }, [attempt]);
  const changeView = (next: MapView) => {
    view.current = next;
    setSelectedView(next);
    engine.current?.setView(next, radius.current);
  };
  const changeRadius = (next: number) => {
    radius.current = next;
    setRadiusNm(next);
    changeView('traffic');
  };
  return (
    <div className="traffic-map">
      <div className="map-toolbar">
        <span className="map-title">
          DTW <span>/ KDTW</span>
        </span>
        <div role="group" aria-label="Map views">
          <button
            aria-pressed={selectedView === 'airport'}
            onClick={() => changeView('airport')}
          >
            Airport view
          </button>
          <button
            aria-pressed={selectedView === 'traffic'}
            onClick={() => changeView('traffic')}
          >
            Traffic view
          </button>
        </div>
        <div className="map-radius-control">
          <label htmlFor={radiusId}>View radius</label>
          <input
            id={radiusId}
            type="range"
            min={MIN_VIEW_RADIUS_NM}
            max={MAX_VIEW_RADIUS_NM}
            step={1}
            value={radiusNm}
            aria-valuetext={`${radiusNm} nautical miles`}
            onChange={(event) =>
              changeRadius(event.currentTarget.valueAsNumber)
            }
            style={{
              backgroundSize: `${((radiusNm - MIN_VIEW_RADIUS_NM) / (MAX_VIEW_RADIUS_NM - MIN_VIEW_RADIUS_NM)) * 100}% 4px, 100% 4px`,
            }}
          />
          <output htmlFor={radiusId}>
            {radiusNm} <span>NM</span>
          </output>
        </div>
      </div>
      <div
        className="geographic-map"
        role="region"
        aria-label={`DTW geographic map. ${aircraft.length} recent observed aircraft.`}
      >
        <div ref={container} className="map-canvas-container" />
        <div className="map-feed-label">
          {mode === 'TEST'
            ? 'Sample aircraft'
            : disconnected
              ? 'No live connection'
              : 'Observed aircraft'}
        </div>
        {status !== 'ready' && (
          <div className="map-load-notice" role="status">
            <strong>
              {status === 'loading'
                ? 'Loading DTW map…'
                : 'Basemap unavailable'}
            </strong>
            <span>
              {status === 'loading'
                ? 'Loading airport geography'
                : 'Check your connection or browser graphics support.'}
            </span>
            {status === 'unavailable' && (
              <button
                onClick={() => {
                  setStatus('loading');
                  setAttempt((value) => value + 1);
                }}
              >
                Retry map
              </button>
            )}
          </div>
        )}
        {!aircraft.length && (
          <div className="map-observation-notice">
            <strong>No current positions available</strong>
          </div>
        )}
        {children}
        {mapProjection && (
          <ul className="sr-only" aria-label="Projected aircraft">
            {supportedPaths(mapProjection).map((path) => (
              <li key={path.aircraftId}>
                {path.label}, proposed path at +
                {mapProjection.minute.toFixed(1)} minutes from the selected
                snapshot.
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="map-caption">
        <div className="map-legend" aria-label="Aircraft legend">
          <span>
            <svg viewBox="-15 -15 30 30" aria-hidden="true">
              <path d="M0-12 2-3 11 3 11 5 2 2 2 8 5 10 5 12 0 10-5 12-5 10-2 8-2 2-11 5-11 3-2-3Z" />
            </svg>
            {mode === 'TEST' ? 'Sample' : 'Observed'}
          </span>
          <span className="legend-stale">
            <svg viewBox="-15 -15 30 30" aria-hidden="true">
              <path d="M0-12 2-3 11 3 11 5 2 2 2 8 5 10 5 12 0 10-5 12-5 10-2 8-2 2-11 5-11 3-2-3Z" />
            </svg>
            Stale
          </span>
          <span>↑ North up</span>
        </div>
        <div ref={credits} className="map-credits" aria-label="Map credits" />
      </div>
    </div>
  );
}
