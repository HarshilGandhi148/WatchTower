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
  it('shows partial-data recommendations while retaining preview checks', () => {
    const suggestion = 'Review available weather and restore aircraft updates.';
    const snapshot: DashboardSnapshot = {
      ...state,
      exchange: {
        ...state.exchange!,
        reports: state.exchange!.reports.map((report) => ({
          ...report,
          outcome: 'insufficient_data',
          suggestion,
        })),
      },
    };
    const { rerender, props } = setup(snapshot);
    rerender(
      <MissionControl
        {...props}
        replay={{
          ...props.replay,
          projection: {
            ...scenarioData().projections[0],
            snapshotId: c.snapshot.snapshot_id,
          },
        }}
      />,
    );
    expect(screen.getByText(suggestion)).toBeInTheDocument();
    expect(screen.getByText(/Limited data/)).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: /View agent conversation/ }),
    );
    expect(screen.getByText('LIMITED DATA')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Accept for TEST path preview' }),
    ).not.toBeInTheDocument();
  });
  it('shows a correction while the previous report is selected and stops its preview', () => {
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
    const exchange = state.exchange!;
    const correcting = {
      ...state,
      exchange: {
        ...exchange,
        activity: [
          ...exchange.activity,
          {
            ...exchange.activity[0],
            id: 'TEST-correction-request',
            round: 2,
            event: 'Reassessment requested',
            summary: 'Round two is now being checked.',
          },
        ],
      },
    };
    rerender(
      <MissionControl
        {...props}
        replay={{ ...props.replay, projection, snapshot: correcting }}
      />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: /View agent conversation/ }),
    );
    expect(
      screen.getByText('Round two is now being checked.'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('PREVIOUS ROUND · REASSESSING'),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Accept for TEST path preview' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('slider', { name: 'Projection minute' }),
    ).not.toBeInTheDocument();
  });
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
  it('shows automatic progress and removes expired advice', () => {
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
    expect(
      screen.queryByRole('button', { name: /View agent conversation/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText('EXPIRED')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /Accept for TEST/ }),
    ).not.toBeInTheDocument();
  });
  it('expires a selected result and closes its conversation as time advances', () => {
    const { rerender, props } = setup();
    fireEvent.click(
      screen.getByRole('button', { name: /View agent conversation/ }),
    );
    expect(
      screen.getByRole('region', { name: 'Assessment summaries' }),
    ).toBeInTheDocument();
    rerender(
      <MissionControl
        {...props}
        replay={{
          ...props.replay,
          now: Date.parse(state.exchange!.reports[0].expires_at_utc),
        }}
      />,
    );
    expect(
      screen.queryByRole('button', { name: /View agent conversation/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('region', { name: 'Assessment summaries' }),
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
