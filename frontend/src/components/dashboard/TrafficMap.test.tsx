import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TrafficMap } from './TrafficMap';
import { scenarioData } from '../../test/scenarios';

const runtime = vi.hoisted(() => ({
  create: vi.fn(),
  update: vi.fn(),
  projection: vi.fn(),
  view: vi.fn(),
  destroy: vi.fn(),
}));
vi.mock('./mapEngine', () => ({ createTrafficMap: runtime.create }));
beforeEach(() => {
  vi.clearAllMocks();
  runtime.create.mockImplementation(() => ({
    updateAircraft: runtime.update,
    updateProjection: runtime.projection,
    setView: runtime.view,
    destroy: runtime.destroy,
  }));
});
const props = () => ({
  aircraft: scenarioData().aircraft,
  now: Date.now(),
  disconnected: false,
  timezone: 'America/Detroit',
  mode: 'TEST' as const,
});
describe('geographic map lifecycle', () => {
  it('retains selected path playback through retries and leaves observed aircraft untouched', async () => {
    const initial = props();
    const projection = scenarioData().projections[0];
    const mapProjection = {
      projection,
      expectedSnapshotId: projection.snapshotId,
      minute: 5,
      mode: 'TEST' as const,
    };
    const view = render(
      <TrafficMap {...initial} mapProjection={mapProjection} />,
    );
    await waitFor(() => expect(runtime.create).toHaveBeenCalledOnce());
    expect(runtime.projection).toHaveBeenLastCalledWith(mapProjection);
    act(() => runtime.create.mock.calls[0][1].unavailable());
    fireEvent.click(screen.getByRole('button', { name: 'Retry map' }));
    await waitFor(() => expect(runtime.create).toHaveBeenCalledTimes(2));
    expect(runtime.projection).toHaveBeenLastCalledWith(mapProjection);
    view.rerender(
      <TrafficMap
        {...initial}
        mapProjection={{ ...mapProjection, minute: 10 }}
      />,
    );
    expect(runtime.projection).toHaveBeenLastCalledWith(
      expect.objectContaining({ minute: 10 }),
    );
    expect(runtime.update).toHaveBeenCalledTimes(2);
    expect(runtime.update).toHaveBeenLastCalledWith(
      expect.objectContaining({ aircraft: initial.aircraft }),
    );
  });
  it('preserves pan/zoom and the selected view while aircraft and freshness change', async () => {
    const initial = props();
    const view = render(<TrafficMap {...initial} />);
    const slider = screen.getByRole('slider', { name: 'View radius' });
    expect(slider).toHaveAttribute('min', '5');
    expect(slider).toHaveAttribute('max', '40');
    fireEvent.change(slider, { target: { value: '20' } });
    expect(slider).toHaveAttribute('aria-valuetext', '20 nautical miles');
    expect(
      screen.getByRole('button', { name: 'Traffic view' }),
    ).toHaveAttribute('aria-pressed', 'true');
    await waitFor(() => expect(runtime.create).toHaveBeenCalledOnce());
    expect(runtime.view).toHaveBeenLastCalledWith('traffic', 20);
    act(() => runtime.create.mock.calls[0][1].ready());
    expect(screen.queryByText('Loading DTW map…')).not.toBeInTheDocument();
    const aircraft = [{ ...initial.aircraft[0], longitudeDeg: -83.36 }];
    view.rerender(
      <TrafficMap
        {...initial}
        aircraft={aircraft}
        now={initial.now + 1000}
        disconnected
      />,
    );
    expect(runtime.create).toHaveBeenCalledOnce();
    expect(runtime.view).toHaveBeenCalledTimes(1);
    expect(slider).toHaveValue('20');
    expect(runtime.update).toHaveBeenLastCalledWith(
      expect.objectContaining({ aircraft, disconnected: true }),
    );
    expect(screen.getByText('Sample aircraft')).toBeVisible();
  });
  it('shows map failures separately, retries and cleans old resources up', async () => {
    const view = render(<TrafficMap {...props()} />);
    await waitFor(() => expect(runtime.create).toHaveBeenCalledOnce());
    fireEvent.change(screen.getByRole('slider', { name: 'View radius' }), {
      target: { value: '5' },
    });
    act(() => runtime.create.mock.calls[0][1].unavailable());
    expect(screen.getByText('Basemap unavailable')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Retry map' }));
    await waitFor(() => expect(runtime.create).toHaveBeenCalledTimes(2));
    expect(runtime.view).toHaveBeenLastCalledWith('traffic', 5);
    expect(screen.getByRole('slider', { name: 'View radius' })).toHaveValue(
      '5',
    );
    expect(runtime.destroy).toHaveBeenCalledOnce();
    // A disposed map cannot re-enable the current map's status.
    act(() => runtime.create.mock.calls[0][1].ready());
    expect(screen.getByText('Loading DTW map…')).toBeVisible();
    act(() => runtime.create.mock.calls[1][1].ready());
    view.unmount();
    expect(runtime.destroy).toHaveBeenCalledTimes(2);
  });
  it('returns to the chosen radius after airport view without changing observations', async () => {
    const initial = props();
    render(<TrafficMap {...initial} />);
    await waitFor(() => expect(runtime.create).toHaveBeenCalledOnce());
    const slider = screen.getByRole('slider', { name: 'View radius' });
    fireEvent.change(slider, { target: { value: '12' } });
    fireEvent.click(screen.getByRole('button', { name: 'Airport view' }));
    expect(runtime.view).toHaveBeenLastCalledWith('airport', 12);
    fireEvent.click(screen.getByRole('button', { name: 'Traffic view' }));
    expect(runtime.view).toHaveBeenLastCalledWith('traffic', 12);
    fireEvent.change(slider, { target: { value: '40' } });
    expect(runtime.view).toHaveBeenLastCalledWith('traffic', 40);
    expect(runtime.create).toHaveBeenCalledOnce();
    expect(runtime.update).toHaveBeenCalledOnce();
    expect(runtime.update).toHaveBeenLastCalledWith(
      expect.objectContaining({ aircraft: initial.aircraft }),
    );
  });
  it('retains the real basemap and truthful no-data message without observations', async () => {
    render(<TrafficMap {...props()} aircraft={[]} mode="LIVE" disconnected />);
    await waitFor(() => expect(runtime.create).toHaveBeenCalledOnce());
    expect(screen.getByText('No current positions available')).toBeVisible();
    expect(screen.getByText('No live connection')).toBeVisible();
    expect(runtime.update).toHaveBeenLastCalledWith(
      expect.objectContaining({ aircraft: [] }),
    );
  });
});
