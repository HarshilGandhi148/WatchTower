import {
  Map as MapLibreMap,
  Marker,
  NavigationControl,
  ScaleControl,
  AttributionControl,
  Popup,
  setWorkerUrl,
  type GeoJSONSource,
} from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import type { Aircraft } from '../../features/live/types';
import { ageLabel, formatTime } from '../../lib/time';
import {
  pathFeatures,
  projectedPosition,
  supportedPaths,
  type MapProjection,
} from '../../features/projection/aircraftPaths';
import {
  DTW_CENTER,
  MAX_VIEW_RADIUS_NM,
  MIN_VIEW_RADIUS_NM,
  rangeRing,
} from './geography';

export type MapView = 'airport' | 'traffic';
export interface AircraftMapUpdate {
  highlightedAircraftIds?: readonly string[];
  aircraft: readonly Aircraft[];
  now: number;
  disconnected: boolean;
  timezone: string;
}
export interface TrafficMapController {
  updateAircraft(update: AircraftMapUpdate): void;
  updateProjection(update: MapProjection | null): void;
  setView(view: MapView, radiusNm: number): void;
  destroy(): void;
}
export const PLANE_PATH =
  'M0 -12C1.4 -12 2 -9.5 2 -7L2 -3L11 2.5L11 5L2 2.5L2 8L5 10L5 12L0 10.5L-5 12L-5 10L-2 8L-2 2.5L-11 5L-11 2.5L-2 -3L-2 -7C-2 -9.5 -1.4 -12 0 -12Z';

export function createTrafficMap(
  container: HTMLElement,
  events: {
    ready: () => void;
    unavailable: () => void;
    selectProjected?: (id: string) => void;
  },
  attributionHost?: HTMLElement,
): TrafficMapController {
  setWorkerUrl(workerUrl);
  const map = new MapLibreMap({
    container,
    style: 'https://tiles.openfreemap.org/styles/dark',
    center: [...DTW_CENTER],
    zoom: 12.8,
    minZoom: 6,
    maxZoom: 17,
    pitch: 0,
    maxPitch: 0,
    bearing: 0,
    dragRotate: false,
    touchPitch: false,
    attributionControl: false,
  });
  map.touchZoomRotate.disableRotation();
  map.keyboard.disableRotation();
  map.addControl(new NavigationControl({ showCompass: false }), 'top-right');
  map.addControl(
    new ScaleControl({ unit: 'nautical', maxWidth: 100 }),
    'bottom-left',
  );
  const attribution = new AttributionControl({ compact: false });
  const attributionAnchor = document.createElement('div');
  // Render source-managed credits in the compact map-edge strip.
  // MapLibre still owns attribution updates and cleanup.
  map.addControl(
    attributionHost
      ? {
          onAdd(currentMap: MapLibreMap) {
            attributionHost.append(attribution.onAdd(currentMap));
            return attributionAnchor;
          },
          onRemove() {
            attribution.onRemove();
            attributionAnchor.remove();
          },
        }
      : attribution,
    'bottom-right',
  );
  const canvas = map.getCanvas();
  canvas.setAttribute(
    'aria-label',
    'DTW geographic map. Use arrow keys to pan and plus or minus to zoom.',
  );
  const markers = new globalThis.Map<
    string,
    {
      marker: Marker;
      button: HTMLButtonElement;
      icon: SVGSVGElement;
      label: HTMLSpanElement;
      popup: Popup;
    }
  >();
  let destroyed = false;
  let failed = false;
  let radiusNm = MAX_VIEW_RADIUS_NM;
  let rangeSource: GeoJSONSource | undefined;
  let projectionSource: GeoJSONSource | undefined;
  let projectionUpdate: MapProjection | null = null;
  let projectionKey = '';
  let fittedBounds: [[number, number], [number, number]] | null = null;
  const projectedMarkers = new globalThis.Map<
    string,
    {
      marker: Marker;
      button: HTMLButtonElement;
      icon: SVGSVGElement;
      label: HTMLSpanElement;
    }
  >();
  let appearanceKey = '';
  const applyPathAppearance = (force = false) => {
    if (!projectionSource) return;
    const branch = projectionUpdate?.branch ?? 'proposed';
    const selected = supportedPaths(projectionUpdate).some(
      (path) => path.aircraftId === projectionUpdate?.selectedAircraftId,
    )
      ? projectionUpdate?.selectedAircraftId
      : null;
    const key = `${branch}/${selected ?? ''}`;
    if (!force && key === appearanceKey) return;
    appearanceKey = key;
    for (const view of ['baseline', 'proposed'] as const) {
      const opacity = view === branch ? 0.95 : 0.22;
      map.setPaintProperty(
        `scenario-${view}`,
        'line-opacity',
        selected
          ? ['case', ['==', ['get', 'aircraftId'], selected], opacity, 0.12]
          : opacity,
      );
      map.setPaintProperty(
        `scenario-${view}`,
        'line-width',
        selected
          ? [
              'case',
              ['==', ['get', 'aircraftId'], selected],
              view === branch ? 3.2 : 1.5,
              1.2,
            ]
          : view === branch
            ? 2.5
            : 1.2,
      );
    }
  };
  const boundsFor = (
    coordinates: number[][],
  ): [[number, number], [number, number]] => [
    [
      Math.min(...coordinates.map((p) => p[0])),
      Math.min(...coordinates.map((p) => p[1])),
    ],
    [
      Math.max(...coordinates.map((p) => p[0])),
      Math.max(...coordinates.map((p) => p[1])),
    ],
  ];
  const fitCurrentBounds = () => {
    const playback =
      container.parentElement?.querySelector<HTMLElement>('.scenario-playback');
    const bottom = playback
      ? Math.min(
          playback.offsetHeight +
            (Number.parseFloat(getComputedStyle(playback).bottom) || 12) +
            12,
          Math.max(35, container.clientHeight - 70),
        )
      : 35;
    if (fittedBounds)
      map.fitBounds(fittedBounds, {
        padding: { top: 35, left: 35, right: 35, bottom },
        duration: 0,
        bearing: 0,
      });
  };
  map.on('movestart', (event) => {
    if ('originalEvent' in event && event.originalEvent) fittedBounds = null;
  });
  const radiusData = () => ({
    type: 'Feature' as const,
    properties: { radiusNm },
    geometry: {
      type: 'LineString' as const,
      coordinates: rangeRing(radiusNm),
    },
  });
  const centerElement = document.createElement('span');
  centerElement.className = 'airport-center-marker';
  centerElement.textContent = 'DTW';
  centerElement.setAttribute('aria-label', 'DTW airport reference point');
  const airportMarker = new Marker({ element: centerElement })
    .setLngLat([...DTW_CENTER])
    .addTo(map);
  map.on('load', () => {
    if (destroyed) return;
    map.addSource('dtw-range-rings', { type: 'geojson', data: radiusData() });
    rangeSource = map.getSource<GeoJSONSource>('dtw-range-rings');
    map.addLayer({
      id: 'dtw-range-rings',
      type: 'line',
      source: 'dtw-range-rings',
      paint: {
        'line-color': '#8ed8c7',
        'line-width': 1,
        'line-opacity': 0.45,
        'line-dasharray': [3, 4],
      },
    });
    map.addSource('scenario-paths', {
      type: 'geojson',
      data: pathFeatures(projectionUpdate),
    });
    projectionSource = map.getSource<GeoJSONSource>('scenario-paths');
    for (const branch of ['baseline', 'proposed'] as const) {
      map.addLayer({
        id: `scenario-${branch}`,
        type: 'line',
        source: 'scenario-paths',
        filter: ['==', ['get', 'branch'], branch],
        paint: {
          'line-color': branch === 'proposed' ? '#86aaf9' : '#8f99ad',
          'line-width': branch === 'proposed' ? 2.5 : 1.5,
          'line-opacity': branch === 'proposed' ? 0.95 : 0.65,
          'line-dasharray': branch === 'proposed' ? [3, 2] : [1, 3],
        },
      });
    }
    applyPathAppearance(true);
    if (!failed) events.ready();
  });
  map.on('error', () => {
    if (!destroyed) {
      failed = true;
      events.unavailable();
    }
  });
  // Keep a tile failure visible until an explicit retry creates a clean map.
  const resize = new ResizeObserver(() => {
    if (!destroyed && container.clientWidth && container.clientHeight) {
      map.resize();
      fitCurrentBounds();
    }
  });
  resize.observe(container);
  return {
    updateProjection(update) {
      if (destroyed) return;
      // Playback moves markers each frame; fixed route geometry needs updating
      // only when its snapshot or supplied projection changes.
      const pathsChanged =
        projectionUpdate?.projection !== update?.projection ||
        projectionUpdate?.expectedSnapshotId !== update?.expectedSnapshotId ||
        projectionUpdate?.mode !== update?.mode;
      projectionUpdate = update;
      const trajectories = supportedPaths(update);
      if (pathsChanged) projectionSource?.setData(pathFeatures(update));
      applyPathAppearance(pathsChanged);
      const active = new Set<string>();
      for (const trajectory of trajectories) {
        const position = projectedPosition(
          trajectory[update?.branch ?? 'proposed'],
          update?.minute ?? 0,
        );
        if (!position) continue;
        active.add(trajectory.aircraftId);
        let entry = projectedMarkers.get(trajectory.aircraftId);
        if (!entry) {
          const element = document.createElement('button');
          element.type = 'button';
          element.className = 'projected-plane-marker';
          element.addEventListener('click', () =>
            events.selectProjected?.(trajectory.aircraftId),
          );
          const icon = document.createElementNS(
            'http://www.w3.org/2000/svg',
            'svg',
          );
          icon.setAttribute('viewBox', '-15 -15 30 30');
          icon.setAttribute('aria-hidden', 'true');
          const path = document.createElementNS(
            'http://www.w3.org/2000/svg',
            'path',
          );
          path.setAttribute('d', PLANE_PATH);
          icon.append(path);
          const label = document.createElement('span');
          element.append(icon, label);
          const marker = new Marker({ element })
            .setLngLat([position.longitudeDeg, position.latitudeDeg])
            .addTo(map);
          entry = { marker, button: element, icon, label };
          projectedMarkers.set(trajectory.aircraftId, entry);
        }
        const selected = update?.selectedAircraftId;
        const branch = update?.branch ?? 'proposed';
        entry.label.textContent = trajectory.label;
        entry.button.dataset.branch = branch;
        entry.button.classList.toggle(
          'selected-projection',
          selected === trajectory.aircraftId,
        );
        entry.button.classList.toggle(
          'dimmed-projection',
          Boolean(
            selected &&
            trajectories.some((path) => path.aircraftId === selected) &&
            selected !== trajectory.aircraftId,
          ),
        );
        entry.button.setAttribute(
          'aria-pressed',
          String(selected === trajectory.aircraftId),
        );
        entry.button.setAttribute(
          'aria-label',
          `${trajectory.label}, projected aircraft in ${branch} scenario. Select route.`,
        );
        entry.icon.style.transform = `rotate(${position.headingDeg}deg)`;
        entry.marker.setLngLat([position.longitudeDeg, position.latitudeDeg]);
      }
      for (const [id, entry] of projectedMarkers) {
        if (!active.has(id)) {
          entry.marker.remove();
          projectedMarkers.delete(id);
        }
      }
      const nextKey = trajectories.length
        ? `${update?.projection?.id}/${update?.expectedSnapshotId}`
        : '';
      if (nextKey !== projectionKey) {
        projectionKey = nextKey;
        if (trajectories.length) {
          fittedBounds = boundsFor(
            trajectories.flatMap((t) =>
              [...t.baseline, ...t.proposed].map((p) => [
                p.longitudeDeg,
                p.latitudeDeg,
              ]),
            ),
          );
          fitCurrentBounds();
        } else fittedBounds = null;
      }
    },
    updateAircraft({
      aircraft,
      now,
      disconnected,
      timezone,
      highlightedAircraftIds = [],
    }) {
      if (destroyed) return;
      const active = new Set(aircraft.map((a) => a.id));
      for (const [id, entry] of markers) {
        if (!active.has(id)) {
          entry.popup.remove();
          entry.marker.remove();
          markers.delete(id);
        }
      }
      for (const aircraftItem of aircraft) {
        let entry = markers.get(aircraftItem.id);
        if (!entry) {
          const button = document.createElement('button');
          button.type = 'button';
          const icon = document.createElementNS(
            'http://www.w3.org/2000/svg',
            'svg',
          );
          icon.setAttribute('viewBox', '-15 -15 30 30');
          icon.setAttribute('aria-hidden', 'true');
          const path = document.createElementNS(
            'http://www.w3.org/2000/svg',
            'path',
          );
          path.setAttribute('d', PLANE_PATH);
          icon.append(path);
          const label = document.createElement('span');
          label.className = 'plane-label';
          button.append(icon, label);
          const popup = new Popup({
            offset: 18,
            closeButton: true,
            maxWidth: '260px',
          });
          const marker = new Marker({ element: button })
            .setLngLat([aircraftItem.longitudeDeg, aircraftItem.latitudeDeg])
            .setPopup(popup)
            .addTo(map);
          // Custom buttons provide Enter/Space activation in addition to pointer use.
          button.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              marker.togglePopup();
            }
          });
          entry = { marker, button, icon, label, popup };
          markers.set(aircraftItem.id, entry);
        }
        const stale =
          disconnected || now - Date.parse(aircraftItem.observedAtUtc) > 60_000;
        const knownTrack =
          aircraftItem.trackDeg !== null &&
          Number.isFinite(aircraftItem.trackDeg) &&
          aircraftItem.trackDeg >= 0 &&
          aircraftItem.trackDeg < 360;
        // Retain MapLibre's positioning class when changing freshness styles.
        entry.button.classList.add('plane-marker');
        entry.button.classList.toggle(
          'evidence-highlight',
          highlightedAircraftIds.includes(aircraftItem.id),
        );
        entry.button.classList.toggle('stale-plane', stale);
        entry.label.textContent = `${aircraftItem.label}${stale ? ' · stale' : ''}`;
        entry.button.setAttribute(
          'aria-label',
          `${aircraftItem.label}, ${stale ? 'stale observation' : 'observed aircraft'}, ${aircraftItem.dtwAssociated ? 'DTW-associated' : 'airport association unknown'}. Open details.`,
        );
        entry.icon.style.transform = `rotate(${knownTrack ? aircraftItem.trackDeg : 0}deg)`;
        entry.marker.setLngLat([
          aircraftItem.longitudeDeg,
          aircraftItem.latitudeDeg,
        ]);
        const details = document.createElement('div');
        const title = document.createElement('strong');
        title.textContent = aircraftItem.label;
        details.append(title);
        for (const text of [
          aircraftItem.dtwAssociated
            ? 'DTW-associated'
            : 'Airport association unknown',
          `${stale ? 'Stale' : 'Observed'} · ${ageLabel(aircraftItem.observedAtUtc, now)}`,
          formatTime(aircraftItem.observedAtUtc, timezone),
          knownTrack
            ? `Observed track ${aircraftItem.trackDeg}° true`
            : 'Track unknown; icon orientation is illustrative.',
          `${aircraftItem.latitudeDeg.toFixed(4)}°, ${aircraftItem.longitudeDeg.toFixed(4)}°`,
        ]) {
          const paragraph = document.createElement('p');
          paragraph.textContent = text;
          details.append(paragraph);
        }
        entry.popup.setDOMContent(details);
      }
    },
    setView(view, nextRadiusNm) {
      if (destroyed) return;
      if (Number.isFinite(nextRadiusNm)) {
        const next = Math.min(
          MAX_VIEW_RADIUS_NM,
          Math.max(MIN_VIEW_RADIUS_NM, Math.round(nextRadiusNm)),
        );
        if (next !== radiusNm) {
          radiusNm = next;
          rangeSource?.setData(radiusData());
        }
      }
      if (view === 'airport') {
        fittedBounds = null;
        map.jumpTo({
          center: [...DTW_CENTER],
          zoom: 12.8,
          bearing: 0,
          pitch: 0,
        });
      } else {
        const ring = rangeRing(radiusNm);
        fittedBounds = boundsFor(ring);
        fitCurrentBounds();
      }
    },
    destroy() {
      destroyed = true;
      resize.disconnect();
      markers.forEach((entry) => {
        entry.popup.remove();
        entry.marker.remove();
      });
      markers.clear();
      projectedMarkers.forEach((entry) => entry.marker.remove());
      projectedMarkers.clear();
      airportMarker.remove();
      map.remove();
    },
  };
}
