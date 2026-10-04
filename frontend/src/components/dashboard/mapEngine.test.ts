import { afterEach, describe, expect, it, vi } from 'vitest';
import { createTrafficMap } from './mapEngine';
import { scenarioData } from '../../test/scenarios';
import { distanceFromDtwNm, rangeRing } from './geography';

const api = vi.hoisted(() => ({
  maps: [] as {
    options: unknown;
    callbacks: Record<string, () => void>;
    controls: unknown[];
    remove: ReturnType<typeof vi.fn>;
    jumpTo: ReturnType<typeof vi.fn>;
    fitBounds: ReturnType<typeof vi.fn>;
    setPaintProperty: ReturnType<typeof vi.fn>;
    sources: Record<
      string,
      { data: unknown; setData: ReturnType<typeof vi.fn> }
    >;
  }[],
  markers: [] as {
    element: HTMLElement;
    popup?: { details?: HTMLElement; removed: boolean };
    position?: number[];
    removed: boolean;
  }[],
  worker: vi.fn(),
  observerDisconnect: vi.fn(),
  resizeCallback: undefined as (() => void) | undefined,
}));
vi.mock('maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url', () => ({
  default: '/test-map-worker.js',
}));
vi.mock('maplibre-gl', () => {
  class Map {
    options: unknown;
    callbacks: Record<string, () => void> = {};
    controls: unknown[] = [];
    touchZoomRotate = { disableRotation: vi.fn() };
    keyboard = { disableRotation: vi.fn() };
    remove = vi.fn(() => {
      this.controls.forEach((control) =>
        (control as { onRemove?: () => void }).onRemove?.(),
      );
    });
    jumpTo = vi.fn();
    fitBounds = vi.fn();
    setPaintProperty = vi.fn();
    sources: Record<
      string,
      { data: unknown; setData: ReturnType<typeof vi.fn> }
    > = {};
    constructor(options: unknown) {
      this.options = options;
      api.maps.push(this);
    }
    addControl(control: unknown) {
      this.controls.push(control);
      (control as { onAdd?: (map: Map) => HTMLElement }).onAdd?.(this);
    }
    getCanvas() {
      return document.createElement('canvas');
    }
    on(name: string, callback: () => void) {
      this.callbacks[name] = callback;
    }
    addSource(id: string, options: { data: unknown }) {
      this.sources[id] = { data: options.data, setData: vi.fn() };
    }
    getSource(id: string) {
      return this.sources[id];
    }
    addLayer() {}
    resize() {}
    areTilesLoaded() {
      return true;
    }
  }
  class Marker {
    element: HTMLElement;
    popup?: Popup;
    position?: number[];
    removed = false;
    constructor({ element }: { element: HTMLElement }) {
      this.element = element;
      this.element.classList.add('maplibregl-marker');
      api.markers.push(this);
    }
    setLngLat(position: number[]) {
      this.position = position;
      return this;
    }
    setPopup(popup: Popup) {
      this.popup = popup;
      return this;
    }
    addTo() {
      return this;
    }
    remove() {
      this.removed = true;
    }
    togglePopup() {}
  }
  class Popup {
    details?: HTMLElement;
    removed = false;
    setDOMContent(details: HTMLElement) {
      this.details = details;
      return this;
    }
    remove() {
      this.removed = true;
    }
  }
  class NavigationControl {}
  class ScaleControl {}
  class AttributionControl {
    element = document.createElement('details');
    constructor(public options: unknown) {}
    onAdd() {
      this.element.textContent =
        'OpenFreeMap © OpenMapTiles Data from OpenStreetMap';
      return this.element;
    }
    onRemove() {
      this.element.remove();
    }
  }
  return {
    Map,
    Marker,
    Popup,
    NavigationControl,
    ScaleControl,
    AttributionControl,
    setWorkerUrl: api.worker,
  };
});
afterEach(() => {
  api.maps.length = 0;
  api.markers.length = 0;
  vi.unstubAllGlobals();
  api.resizeCallback = undefined;
});
function create(
  events: Parameters<typeof createTrafficMap>[1] = {
    ready: vi.fn(),
    unavailable: vi.fn(),
  },
  attributionHost?: HTMLElement,
) {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(callback: () => void) {
        api.resizeCallback = callback;
      }
      observe() {}
      disconnect() {
        api.observerDisconnect();
      }
    },
  );
  return createTrafficMap(
    document.createElement('div'),
    events,
    attributionHost,
  );
}
describe('aircraft map rendering boundary', () => {
  it('draws separate scenario paths and moves projected planes without moving observations', () => {
    const controller = create();
    const data = scenarioData();
    controller.updateAircraft({
      aircraft: data.aircraft,
      now: Date.now(),
      disconnected: false,
      timezone: 'America/Detroit',
    });
    const observed = api.markers.filter((m) =>
      m.element.classList.contains('plane-marker'),
    );
    const before = observed.map((m) => [...m.position!]);
    const update = {
      projection: data.projections[0],
      expectedSnapshotId: 'test-snapshot-1',
      minute: 0,
      mode: 'TEST' as const,
    };
    controller.updateProjection(update);
    const ghosts = api.markers.filter((m) =>
      m.element.classList.contains('projected-plane-marker'),
    );
    expect(ghosts).toHaveLength(3);
    api.maps[0].callbacks.load();
    expect(api.maps[0].sources['scenario-paths'].data).toEqual(
      expect.objectContaining({
        type: 'FeatureCollection',
        features: expect.arrayContaining([
          expect.objectContaining({
            properties: { aircraftId: 'test-plane-1', branch: 'proposed' },
          }),
        ]),
      }),
    );
    controller.updateProjection({ ...update, minute: 7.5 });
    expect(ghosts[0].position).not.toEqual(before[0]);
    expect(observed.map((m) => m.position)).toEqual(before);
    expect(api.maps[0].fitBounds).toHaveBeenCalledOnce(); // Playback preserves the camera.
    controller.updateProjection(null);
    expect(ghosts.every((m) => m.removed)).toBe(true);
    expect(observed.every((m) => !m.removed)).toBe(true);
    expect(
      api.maps[0].sources['scenario-paths'].setData.mock.lastCall?.[0].features,
    ).toEqual([]);
    controller.destroy();
  });
  it('keeps fixed path geometry cached during playback and clears it when mode changes', () => {
    const controller = create();
    api.maps[0].callbacks.load();
    const projection = scenarioData().projections[0];
    const update = {
      projection,
      expectedSnapshotId: projection.snapshotId,
      minute: 0,
      mode: 'TEST' as const,
    };
    const source = api.maps[0].sources['scenario-paths'];
    controller.updateProjection(update);
    expect(source.setData).toHaveBeenCalledOnce();
    for (const minute of [0.1, 0.2, 0.3])
      controller.updateProjection({ ...update, minute });
    expect(source.setData).toHaveBeenCalledOnce();
    controller.updateProjection({ ...update, mode: 'LIVE' });
    expect(source.setData).toHaveBeenCalledTimes(2);
    expect(source.setData.mock.lastCall?.[0].features).toEqual([]);
    controller.updateProjection({
      ...update,
      mode: 'LIVE',
      projection: {
        ...projection,
        aircraftPaths: { ...projection.aircraftPaths!, basis: 'modeled' },
      },
    });
    expect(source.setData).toHaveBeenCalledTimes(3);
    expect(source.setData.mock.lastCall?.[0].features).toHaveLength(6);
    controller.destroy();
  });
  it('switches projected branches and highlights selected routes without changing the camera or observations', () => {
    const selectProjected = vi.fn();
    const controller = create({
      ready: vi.fn(),
      unavailable: vi.fn(),
      selectProjected,
    });
    api.maps[0].callbacks.load();
    const data = scenarioData();
    controller.updateAircraft({
      aircraft: data.aircraft,
      now: Date.now(),
      disconnected: false,
      timezone: 'America/Detroit',
    });
    const observed = api.markers.filter((marker) =>
      marker.element.classList.contains('plane-marker'),
    );
    const before = observed.map((marker) => [...marker.position!]);
    const update = {
      projection: data.projections[0],
      expectedSnapshotId: 'test-snapshot-1',
      minute: 5,
      mode: 'TEST' as const,
      branch: 'proposed' as const,
    };
    controller.updateProjection(update);
    const ghosts = api.markers.filter((marker) =>
      marker.element.classList.contains('projected-plane-marker'),
    );
    expect(ghosts[0].position).toEqual([-83.34, 42.25]);
    controller.updateProjection({
      ...update,
      branch: 'baseline',
      selectedAircraftId: 'test-plane-1',
    });
    expect(ghosts[0].position).toEqual([-83.32, 42.27]);
    expect(ghosts[0].element).toHaveClass('selected-projection');
    expect(ghosts[1].element).toHaveClass('dimmed-projection');
    expect(ghosts[0].element).toHaveAttribute('aria-pressed', 'true');
    expect(ghosts[0].element).toHaveAttribute(
      'aria-label',
      'DEMO101, projected aircraft in baseline scenario. Select route.',
    );
    ghosts[0].element.click();
    expect(selectProjected).toHaveBeenCalledWith('test-plane-1');
    expect(api.maps[0].fitBounds).toHaveBeenCalledOnce();
    expect(
      api.maps[0].sources['scenario-paths'].setData,
    ).toHaveBeenCalledOnce();
    expect(api.maps[0].setPaintProperty).toHaveBeenCalledWith(
      'scenario-baseline',
      'line-opacity',
      ['case', ['==', ['get', 'aircraftId'], 'test-plane-1'], 0.95, 0.12],
    );
    expect(observed.map((marker) => marker.position)).toEqual(before);
    controller.destroy();
  });
  it('rejects mismatched snapshot paths and illustrative paths in live mode', () => {
    const controller = create();
    const projection = scenarioData().projections[0];
    controller.updateProjection({
      projection,
      expectedSnapshotId: 'wrong',
      minute: 5,
      mode: 'TEST',
    });
    controller.updateProjection({
      projection,
      expectedSnapshotId: projection.snapshotId,
      minute: 5,
      mode: 'LIVE',
    });
    expect(api.markers).toHaveLength(1);
    expect(api.maps[0].fitBounds).not.toHaveBeenCalled();
    controller.destroy();
  });
  it('refits preset bounds as the map resizes beside a larger menu', () => {
    const controller = create();
    const container = (api.maps[0].options as { container: HTMLElement })
      .container;
    Object.defineProperties(container, {
      clientWidth: { value: 480 },
      clientHeight: { value: 600 },
    });
    controller.setView('traffic', 20);
    api.resizeCallback?.();
    expect(api.maps[0].fitBounds).toHaveBeenCalledTimes(2);
    expect(api.maps[0].fitBounds.mock.calls[1][0]).toEqual(
      api.maps[0].fitBounds.mock.calls[0][0],
    );
    controller.destroy();
    api.resizeCallback?.();
    expect(api.maps[0].fitBounds).toHaveBeenCalledTimes(2);
  });
  it('uses the geographic basemap and keeps attribution visible', () => {
    const controller = create();
    expect(api.maps[0].options).toEqual(
      expect.objectContaining({
        style: 'https://tiles.openfreemap.org/styles/dark',
        center: [-83.35339333333333, 42.21243111111111],
      }),
    );
    expect(api.maps[0].controls).toContainEqual(
      expect.objectContaining({ options: { compact: false } }),
    );
    expect(api.worker).toHaveBeenCalledWith('/test-map-worker.js');
    controller.destroy();
  });
  it('mounts provider credits beside the map and cleans them up on disposal', () => {
    const host = document.createElement('div');
    const controller = create(undefined, host);
    expect(host.textContent).toContain('OpenStreetMap');
    expect(host.textContent).toContain('OpenFreeMap');
    controller.destroy();
    expect(host.childElementCount).toBe(0);
  });
  it('updates coordinates without recreating markers and distinguishes stale/unknown track', () => {
    const controller = create();
    const now = Date.now();
    const a = scenarioData(now).aircraft[0];
    controller.updateAircraft({
      aircraft: [a],
      now,
      disconnected: false,
      timezone: 'America/Detroit',
    });
    const plane = api.markers[1];
    expect(plane.position).toEqual([a.longitudeDeg, a.latitudeDeg]);
    expect(plane.element.querySelector('svg')?.style.transform).toBe(
      'rotate(29deg)',
    );
    controller.updateAircraft({
      aircraft: [{ ...a, longitudeDeg: -83.36, trackDeg: null }],
      now: now + 80_000,
      disconnected: false,
      timezone: 'America/Detroit',
    });
    expect(api.markers).toHaveLength(2); // Airport center plus the original aircraft marker.
    expect(plane.position).toEqual([-83.36, a.latitudeDeg]);
    expect(plane.element.className).toContain('stale-plane');
    expect(plane.element.className).toContain('maplibregl-marker');
    expect(plane.popup?.details?.textContent).toContain('Track unknown');
    controller.updateAircraft({
      aircraft: [],
      now,
      disconnected: true,
      timezone: 'America/Detroit',
    });
    expect(plane.removed).toBe(true);
    controller.destroy();
    expect(api.maps[0].remove).toHaveBeenCalledOnce();
  });
  it('keeps failed tiles visibly unavailable even if the remaining map loads', () => {
    const events = { ready: vi.fn(), unavailable: vi.fn() };
    const controller = create(events);
    api.maps[0].callbacks.error();
    api.maps[0].callbacks.load();
    expect(events.unavailable).toHaveBeenCalledOnce();
    expect(events.ready).not.toHaveBeenCalled();
    controller.destroy();
  });
  it('fits the selected geographic radius before load and updates its ring afterward', () => {
    const controller = create();
    controller.setView('traffic', 20);
    api.maps[0].callbacks.load();
    const source = api.maps[0].sources['dtw-range-rings'];
    expect(source.data).toEqual(
      expect.objectContaining({
        properties: { radiusNm: 20 },
        geometry: { type: 'LineString', coordinates: rangeRing(20) },
      }),
    );
    const originalBounds = api.maps[0].fitBounds.mock.calls[0][0] as number[][];
    controller.setView('traffic', 5);
    const ring = source.setData.mock.calls[0][0].geometry
      .coordinates as number[][];
    for (const [longitude, latitude] of ring) {
      expect(distanceFromDtwNm(latitude, longitude)).toBeCloseTo(5, 6);
    }
    const bounds = api.maps[0].fitBounds.mock.lastCall?.[0] as number[][];
    expect(bounds[0][0]).toBeGreaterThan(originalBounds[0][0]);
    expect(bounds[1][0]).toBeLessThan(originalBounds[1][0]);
    expect(api.maps).toHaveLength(1);
    controller.destroy();
  });
  it('bounds the viewer to available coverage and ignores invalid radius values', () => {
    const controller = create();
    api.maps[0].callbacks.load();
    const source = api.maps[0].sources['dtw-range-rings'];
    controller.setView('traffic', 100);
    expect(source.setData).not.toHaveBeenCalled(); // Default remains at 40 NM.
    controller.setView('traffic', -5);
    expect(source.setData.mock.lastCall?.[0].properties.radiusNm).toBe(5);
    controller.setView('traffic', Number.NaN);
    expect(source.setData).toHaveBeenCalledOnce();
    for (const [bounds] of api.maps[0].fitBounds.mock.calls) {
      expect((bounds as number[][]).flat().every(Number.isFinite)).toBe(true);
    }
    controller.destroy();
    controller.setView('traffic', 20);
    expect(api.maps[0].fitBounds).toHaveBeenCalledTimes(3);
  });
  it('renders source labels as text rather than injected HTML', () => {
    const controller = create();
    const now = Date.now();
    const a = scenarioData(now).aircraft[0];
    controller.updateAircraft({
      aircraft: [{ ...a, label: '<img src=x onerror=approve()>' }],
      now,
      disconnected: false,
      timezone: 'America/Detroit',
    });
    expect(api.markers[1].element.querySelector('img')).toBeNull();
    expect(api.markers[1].popup?.details?.querySelector('img')).toBeNull();
    expect(api.markers[1].popup?.details?.textContent).toContain(
      '<img src=x onerror=approve()>',
    );
    controller.destroy();
  });
});
