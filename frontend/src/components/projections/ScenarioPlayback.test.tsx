import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ScenarioPlayback } from './ScenarioPlayback';
import { scenarioData } from '../../test/scenarios';
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
const props = () => ({
  projection: scenarioData().projections[0],
  expectedSnapshotId: 'test-snapshot-1',
  title: 'Release timing',
  minute: 0,
  mode: 'TEST' as const,
  timezone: 'America/Detroit',
  now: Date.now(),
  onMinuteChange: vi.fn(),
  onClose: vi.fn(),
  onDetails: vi.fn(),
});
describe('map scenario playback', () => {
  it('plays to the horizon, pauses on scrubbing and cleans up timers', () => {
    vi.useFakeTimers();
    const data = props();
    const view = render(
      <ScenarioPlayback {...data} acceptedBy="Controller A" />,
    );
    expect(
      screen.getByRole('button', { name: 'Pause projection' }),
    ).toBeEnabled();
    act(() => vi.advanceTimersByTime(1600));
    expect(data.onMinuteChange).toHaveBeenLastCalledWith(1);
    fireEvent.change(screen.getByRole('slider', { name: 'Projected time' }), {
      target: { value: '10' },
    });
    expect(data.onMinuteChange).toHaveBeenLastCalledWith(10);
    expect(
      screen.getByRole('button', { name: 'Play projection' }),
    ).toBeEnabled();
    act(() => vi.advanceTimersByTime(2000));
    expect(data.onMinuteChange).toHaveBeenLastCalledWith(10);
    fireEvent.click(screen.getByRole('button', { name: 'Play projection' }));
    act(() => vi.advanceTimersByTime(25_000));
    expect(data.onMinuteChange).toHaveBeenLastCalledWith(15);
    expect(
      screen.getByRole('button', { name: 'Play projection' }),
    ).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Play projection' }));
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
  it('advances on browser frames and cancels the pending frame when paused', () => {
    vi.useFakeTimers();
    const cancelFrame = vi.spyOn(window, 'cancelAnimationFrame');
    const data = props();
    const view = render(
      <ScenarioPlayback {...data} acceptedBy="Controller A" />,
    );
    act(() => vi.advanceTimersByTime(80));
    expect(data.onMinuteChange.mock.calls.length).toBeGreaterThan(1);
    const times = data.onMinuteChange.mock.calls.map(
      ([minute]) => minute as number,
    );
    expect(
      times.every((minute, index) => index === 0 || minute > times[index - 1]),
    ).toBe(true);
    expect(times.at(-1)).toBeLessThanOrEqual(0.05);
    fireEvent.click(screen.getByRole('button', { name: 'Pause projection' }));
    const count = data.onMinuteChange.mock.calls.length;
    act(() => vi.advanceTimersByTime(1000));
    expect(data.onMinuteChange).toHaveBeenCalledTimes(count);
    expect(cancelFrame).toHaveBeenCalled();
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
  it('pauses immediately when reduced motion is enabled during playback', () => {
    vi.useFakeTimers();
    const listeners = new Set<(event: MediaQueryListEvent) => void>();
    vi.stubGlobal('matchMedia', () => ({
      matches: false,
      addEventListener: (
        _: string,
        callback: (event: MediaQueryListEvent) => void,
      ) => listeners.add(callback),
      removeEventListener: (
        _: string,
        callback: (event: MediaQueryListEvent) => void,
      ) => listeners.delete(callback),
    }));
    const data = props();
    const view = render(
      <ScenarioPlayback {...data} acceptedBy="Controller A" />,
    );
    act(() => vi.advanceTimersByTime(80));
    const count = data.onMinuteChange.mock.calls.length;
    act(() =>
      listeners.forEach((listener) =>
        listener({ matches: true } as MediaQueryListEvent),
      ),
    );
    expect(
      screen.getByRole('button', { name: 'Play projection' }),
    ).toBeEnabled();
    act(() => vi.advanceTimersByTime(1000));
    expect(data.onMinuteChange).toHaveBeenCalledTimes(count);
    view.unmount();
    expect(listeners.size).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
  });
  it('shows unavailable paths and keeps unsupported playback disabled', () => {
    const data = props();
    render(<ScenarioPlayback {...data} mode="LIVE" />);
    expect(
      screen.getByText('Aircraft paths unavailable for this snapshot'),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Play projection' }),
    ).toBeDisabled();
    fireEvent.keyDown(
      screen.getByRole('button', { name: 'Close projection' }),
      { key: 'Escape' },
    );
    expect(data.onClose).toHaveBeenCalledOnce();
    expect(data.onMinuteChange).not.toHaveBeenCalled();
  });
  it('does not autoplay when reduced motion is requested', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    render(<ScenarioPlayback {...props()} acceptedBy="Controller A" />);
    expect(
      screen.getByRole('button', { name: 'Play projection' }),
    ).toBeEnabled();
  });
});
