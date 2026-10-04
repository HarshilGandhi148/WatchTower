import { describe, expect, it } from 'vitest';
import {
  pathFeatures,
  projectedPosition,
  supportedPaths,
  type MapProjection,
} from './aircraftPaths';
import { scenarioData } from '../../test/scenarios';
const update = (): MapProjection => ({
  projection: scenarioData().projections[0],
  expectedSnapshotId: 'test-snapshot-1',
  minute: 0,
  mode: 'TEST',
});
describe('aircraft projection boundary', () => {
  it('interpolates model waypoints without extrapolating outside supplied times', () => {
    const path = supportedPaths(update())[0].proposed;
    const position = projectedPosition(path, 2.5);
    expect(position?.latitudeDeg).toBeCloseTo(
      (path[0].latitudeDeg + path[1].latitudeDeg) / 2,
    );
    expect(position?.longitudeDeg).toBeCloseTo(
      (path[0].longitudeDeg + path[1].longitudeDeg) / 2,
    );
    expect(projectedPosition(path, -1)).toBeNull();
    expect(projectedPosition(path, 16)).toBeNull();
    expect(projectedPosition(path, Number.NaN)).toBeNull();
  });
  it('rejects mismatched snapshots and keeps illustrative paths out of live mode', () => {
    const data = update();
    expect(
      supportedPaths({ ...data, expectedSnapshotId: 'another-snapshot' }),
    ).toEqual([]);
    expect(supportedPaths({ ...data, mode: 'LIVE' })).toEqual([]);
    expect(supportedPaths({ ...data, projection: undefined })).toEqual([]);
    expect(
      supportedPaths({
        ...data,
        projection: { ...data.projection!, aircraftPaths: undefined },
      }),
    ).toEqual([]);
    expect(
      supportedPaths({
        ...data,
        mode: 'LIVE',
        projection: {
          ...data.projection!,
          aircraftPaths: {
            ...data.projection!.aircraftPaths!,
            basis: 'modeled',
          },
        },
      }),
    ).toHaveLength(3);
  });
  it('excludes malformed coordinates, out-of-order paths and duplicate aircraft IDs', () => {
    const data = update();
    const path = data.projection!.aircraftPaths!.trajectories[0];
    const malformed = [
      {
        ...path,
        aircraftId: 'invalid-coordinate',
        proposed: path.proposed.map((p) => ({ ...p, latitudeDeg: Number.NaN })),
      },
      {
        ...path,
        aircraftId: 'invalid-time',
        proposed: [...path.proposed].reverse(),
      },
    ];
    const projection = {
      ...data.projection!,
      aircraftPaths: {
        ...data.projection!.aircraftPaths!,
        trajectories: [path, path, ...malformed],
      },
    };
    expect(supportedPaths({ ...data, projection })).toEqual([path]);
    expect(projectedPosition(malformed[0].proposed, 5)).toBeNull();
  });
  it('keeps path coordinates and live aircraft immutable while building map geometry', () => {
    const data = update();
    const before = structuredClone(data);
    const features = pathFeatures(data);
    expect(features.features).toHaveLength(6);
    expect(features.features.map((f) => f.properties.branch)).toContain(
      'baseline',
    );
    expect(features.features.map((f) => f.properties.branch)).toContain(
      'proposed',
    );
    expect(data).toEqual(before);
  });
});
