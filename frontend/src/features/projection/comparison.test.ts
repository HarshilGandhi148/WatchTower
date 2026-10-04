import { describe, expect, it } from 'vitest';
import { scenarioData } from '../../test/scenarios';
import { displayValue, horizonOutcomes, modelSample } from './comparison';
describe('fixed-snapshot comparisons', () => {
  it('uses paired exact samples and retains adverse tradeoffs', () => {
    const projection = scenarioData().projections[0];
    expect(modelSample(projection, 7.5).minute).toBe(5);
    expect(
      horizonOutcomes(projection).metrics.map((metric) => metric.change),
    ).toEqual([-6, 13, 0]);
  });
  it('does not fill a missing +15 sample with earlier numbers', () => {
    const projection = scenarioData().projections[0];
    const result = horizonOutcomes({
      ...projection,
      proposed: projection.proposed.filter((point) => point.minute !== 15),
    });
    expect(
      result.metrics.every(
        (metric) => metric.proposed === undefined && metric.change === null,
      ),
    ).toBe(true);
  });
  it('shows unavailable values rather than inventing deltas from null or nonfinite inputs', () => {
    const projection = scenarioData().projections[0];
    const result = horizonOutcomes({
      ...projection,
      proposed: projection.proposed.map((point) => ({
        ...point,
        totalWaitingAircraftMinutes: NaN,
      })),
    });
    expect(
      result.metrics.find(
        (metric) => metric.key === 'totalWaitingAircraftMinutes',
      )?.change,
    ).toBeNull();
    expect(displayValue(null, 'aircraft')).toBe(
      'Not estimable with available data',
    );
    expect(displayValue(Infinity, 'aircraft')).toBe(
      'Not estimable with available data',
    );
    expect(displayValue(0, 'aircraft')).toBe('0 aircraft');
  });
  it('uses pressure only for density results with unavailable departure metrics', () => {
    const result = horizonOutcomes(scenarioData().projections[1]);
    expect(result.densityOnly).toBe(true);
    expect(result.metrics).toHaveLength(1);
    expect(result.metrics[0].key).toBe('pressure');
    expect(result.metrics[0].change).toBeCloseTo(-0.3);
  });
});
