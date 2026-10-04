import { describe, expect, it } from 'vitest';
import {
  currentAircraft,
  distanceFromDtwNm,
  DTW_CENTER,
  rangeRing,
} from './geography';
import { scenarioData } from '../../test/scenarios';

describe('geographic observation boundary', () => {
  it('centers on the FAA airport reference point and produces nautical-mile range rings', () => {
    expect(distanceFromDtwNm(DTW_CENTER[1], DTW_CENTER[0])).toBe(0);
    const ring = rangeRing(40);
    for (const [longitude, latitude] of ring)
      expect(distanceFromDtwNm(latitude, longitude)).toBeCloseTo(40, 5);
    expect(ring[0][0]).toBeCloseTo(ring.at(-1)![0], 6);
    expect(ring[0][1]).toBeCloseTo(ring.at(-1)![1], 6);
  });
  it('excludes absent/out-of-range coordinates, out-of-area aircraft and expired positions', () => {
    const now = Date.now();
    const a = scenarioData(now).aircraft[0];
    const positions = currentAircraft(
      [
        a,
        { ...a, id: 'invalid', latitudeDeg: NaN },
        { ...a, id: 'invalid-range', latitudeDeg: 120 },
        { ...a, id: 'wrong-longitude', longitudeDeg: 240 },
        { ...a, id: 'outside', latitudeDeg: 45 },
        {
          ...a,
          id: 'old',
          observedAtUtc: new Date(now - 121_000).toISOString(),
        },
        {
          ...a,
          id: 'future',
          observedAtUtc: new Date(now + 10_000).toISOString(),
        },
      ],
      now,
    );
    expect(positions.map((p) => p.id)).toEqual([a.id]);
  });
});
