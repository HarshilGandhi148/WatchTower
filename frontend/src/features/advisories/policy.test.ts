import { describe, expect, it } from 'vitest';
import { activeAdvisories, validParameter } from './policy';
import { scenarioData } from '../../test/scenarios';
describe('advisory queue policy', () => {
  it('prioritizes urgency and excludes expired or superseded current versions', () => {
    const data = scenarioData();
    const first = data.advisories[0];
    const queue = activeAdvisories(
      [...data.advisories, { ...first, version: 2, status: 'superseded' }],
      Date.now(),
    );
    expect(queue[0].priority).toBe('time_sensitive');
    expect(queue.some((a) => a.id === first.id)).toBe(false);
    expect(
      activeAdvisories([{ ...first, expiresAtUtc: 'invalid' }], Date.now()),
    ).toEqual([]);
  });
  it('does not permit nonfinite, out-of-range or off-step values', () => {
    const parameter = scenarioData().advisories[0].parameters[0];
    expect(validParameter(parameter, 2.5)).toBe(true);
    expect(validParameter(parameter, 2.2)).toBe(false);
    expect(validParameter(parameter, Infinity)).toBe(false);
    expect(validParameter(parameter, 4)).toBe(false);
  });
});
