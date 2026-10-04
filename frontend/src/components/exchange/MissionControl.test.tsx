import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { scenarioData } from '../../test/scenarios';
import { MissionControl } from './MissionControl';
import { createDashboardClient, emptyData } from '../../lib/connection';
import { exchangeData } from '../../features/exchange/adapter';
import captures from '../../../tests/replay-captures.json';
import type { DashboardSnapshot } from '../../features/live/types';
vi.mock('../dashboard/TrafficMap', () => ({
  TrafficMap: () => <div data-testid="preserved-map">Aircraft map</div>,
}));
const c = captures[0];
const state: DashboardSnapshot = {
  ...emptyData,
  mode: 'TEST',
  connection: 'ready',
  connectionMessage: 'TEST',
  exchange: exchangeData(c.rows),
};
function setup(snapshot = state) {
  const load = vi.fn().mockResolvedValue({
    snapshotId: c.snapshot.snapshot_id,
    windowStartUtc: c.snapshot.window_start_utc,
    windowEndUtc: c.snapshot.window_end_utc,
    evidenceIds: c.snapshot.evidence_ids,
    content: c.snapshot,
  });
  const props = {
    client: createDashboardClient(),
    replay: {
      snapshot,
      now: Date.parse(c.capturedAtUtc) + 10000,
      controls: null,
    },
    evidenceLoader: load,
  };
  return { load, props, ...render(<MissionControl {...props} />) };
}
describe('mission control', () => {
  it('preserves the map and labels replay, bot roles and informational result', () => {
    setup();
    expect(screen.getByTestId('preserved-map')).toBeInTheDocument();
    expect(screen.getByText('TEST REPLAY')).toBeInTheDocument();
    expect(
      screen.queryByRole('region', { name: 'Assessment summaries' }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: /View agent conversation/ }),
    );
    expect(screen.getByText(/no operational approval/)).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /approve/i }),
    ).not.toBeInTheDocument();
  });
  it('loads copied evidence on demand and restores keyboard focus', async () => {
    const { load } = setup();
    fireEvent.click(
      screen.getByRole('button', { name: /View agent conversation/ }),
    );
    const button = screen.getAllByRole('button', {
      name: /evidence references/,
    })[0];
    button.focus();
    fireEvent.click(button);
    await waitFor(() =>
      expect(screen.getByText('Analysis window (UTC)')).toBeInTheDocument(),
    );
    expect(load).toHaveBeenCalledWith(c.snapshot.snapshot_id);
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(button).toHaveFocus();
  });
  it('shows automatic progress and marks expired advice', () => {
    const { rerender, props } = setup({ ...state, exchange: undefined });
    expect(
      screen.getByText('Agents are assessing the situation…'),
    ).toBeInTheDocument();
    rerender(
      <MissionControl
        {...props}
        replay={{
          ...props.replay,
          snapshot: state,
          now: Date.parse(c.capturedAtUtc) + 3600000,
        }}
      />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: /View agent conversation/ }),
    );
    expect(screen.getByText('EXPIRED')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /Accept for TEST/ }),
    ).not.toBeInTheDocument();
  });
  it('opens TEST path playback on acceptance and removes it on close', () => {
    const { rerender, props } = setup();
    const projection = {
      ...scenarioData().projections[0],
      snapshotId: c.snapshot.snapshot_id,
    };
    rerender(
      <MissionControl {...props} replay={{ ...props.replay, projection }} />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Accept for TEST path preview' }),
    );
    expect(
      screen.getByRole('slider', { name: 'Projection minute' }),
    ).toHaveValue('0');
    fireEvent.change(
      screen.getByRole('slider', { name: 'Projection minute' }),
      { target: { value: '10' } },
    );
    expect(
      screen.getByText('Illustrative TEST paths · +10.0 min'),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close preview' }));
    expect(
      screen.queryByRole('slider', { name: 'Projection minute' }),
    ).not.toBeInTheDocument();
  });
});
