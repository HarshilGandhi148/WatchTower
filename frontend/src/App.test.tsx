import type { ReactNode } from 'react';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { createDashboardClient } from './lib/connection';
import { createTestServer } from './test/TestServer';
import { scenarioData } from './test/scenarios';

// The WebGL renderer has dedicated lifecycle/rendering tests; dashboard flow
// tests use a lightweight map boundary without a graphics context.
vi.mock('./components/dashboard/TrafficMap', () => ({
  TrafficMap: ({
    aircraft,
    children,
  }: {
    aircraft: readonly unknown[];
    children?: ReactNode;
  }) => (
    <div
      role="region"
      aria-label={`DTW geographic map. ${aircraft.length} recent observed aircraft.`}
    >
      {!aircraft.length && <strong>No current positions available</strong>}
      {children}
    </div>
  ),
}));

afterEach(() => {
  vi.useRealTimers();
  window.history.replaceState(null, '', window.location.pathname);
});
function setup(identity = 'test-controller-a') {
  const server = createTestServer();
  const client = createDashboardClient(server.transport(identity), 'TEST');
  const view = render(<App client={client} />);
  const firstCard = () =>
    within(screen.getByRole('region', { name: 'Advisory list' })).getByRole(
      'article',
      { name: 'Review departure release window' },
    );
  return { ...view, server, client, firstCard };
}
describe('controller dashboard', () => {
  it('loads a direct History link and follows browser navigation without losing observations', () => {
    window.history.replaceState(null, '', '#/history');
    const h = setup();
    const before = structuredClone(h.server.getData().aircraft);
    expect(
      screen.getByRole('heading', { name: 'Decision history' }),
    ).toHaveFocus();
    expect(
      screen.queryByRole('region', { name: /3 recent observed aircraft/ }),
    ).not.toBeInTheDocument();
    act(() => {
      window.history.replaceState(null, '', '#/overview');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    expect(
      screen.getByRole('region', { name: /3 recent observed aircraft/ }),
    ).toBeVisible();
    act(() => {
      window.history.replaceState(null, '', '#/history');
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    });
    expect(
      screen.getByRole('heading', { name: 'Decision history' }),
    ).toBeVisible();
    expect(h.server.getData().aircraft).toEqual(before);
    expect(h.server.calls).toEqual([]);
  });

  it('keeps the map mounted and puts expanded menus beside it', () => {
    const h = setup();
    const map = screen.getByRole('region', {
      name: /3 recent observed aircraft/,
    });
    const queue = h.firstCard().closest('.advisory-panel');
    fireEvent.click(
      within(h.firstCard()).getByRole('button', { name: 'Preview' }),
    );
    expect(
      screen.getByRole('region', { name: 'Map projection' }),
    ).toBeVisible();
    expect(
      screen.queryByRole('region', { name: 'Scenario details' }),
    ).not.toBeInTheDocument();
    const detailsTrigger = screen.getByRole('button', {
      name: 'Scenario details',
    });
    detailsTrigger.focus();
    fireEvent.click(detailsTrigger);
    expect(queue).toHaveAttribute('hidden');
    expect(map.closest('.live-panel')).not.toHaveAttribute('inert');
    expect(
      screen.getByRole('region', { name: 'Scenario details' }),
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'Close scenario details' }),
    );
    expect(detailsTrigger).toHaveFocus();
    expect(queue).not.toHaveAttribute('hidden');
    fireEvent.click(
      within(h.firstCard()).getByRole('button', { name: 'Edit' }),
    );
    const details = screen.getByRole('region', { name: 'Advisory details' });
    fireEvent.click(within(details).getByRole('button', { name: 'Preview' }));
    expect(
      screen.queryByRole('region', { name: 'Advisory details' }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    expect(
      screen.getByRole('heading', { name: 'Decision history' }),
    ).toHaveFocus();
    expect(window.location.hash).toBe('#/history');
    expect(
      screen.queryByRole('region', { name: /3 recent observed aircraft/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Overview' }));
    expect(
      screen.queryByRole('heading', { name: 'Decision history' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'Traffic overview' }),
    ).toHaveFocus();
    expect(
      screen.getByRole('region', { name: /3 recent observed aircraft/ }),
    ).toBe(map);
    expect(h.server.calls).toEqual([]);
  });
  it('opens advisory evidence in a drawer and returns keyboard focus to its trigger', () => {
    const h = setup();
    const trigger = within(h.firstCard()).getByRole('button', {
      name: 'Review details',
    });
    trigger.focus();
    fireEvent.click(trigger);
    const drawer = screen.getByRole('region', { name: 'Advisory details' });
    expect(
      within(drawer).getByRole('button', { name: 'Close advisory details' }),
    ).toHaveFocus();
    expect(within(drawer).getByText(/test-evidence-1/)).toBeVisible();
    expect(
      screen.getByRole('region', { name: /3 recent observed aircraft/ }),
    ).toBeVisible();
    fireEvent.keyDown(
      within(drawer).getByRole('button', { name: 'Close advisory details' }),
      { key: 'Escape' },
    );
    expect(
      screen.queryByRole('region', { name: 'Advisory details' }),
    ).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
  it('retains an expired version in the edit drawer with decisions disabled', () => {
    vi.useFakeTimers();
    const h = setup();
    const data = h.server.getData();
    act(() =>
      h.server.setData({
        ...data,
        advisories: data.advisories.map((a) => ({
          ...a,
          expiresAtUtc: new Date(Date.now() + 2000).toISOString(),
        })),
      }),
    );
    fireEvent.click(
      within(h.firstCard()).getByRole('button', { name: 'Edit' }),
    );
    act(() => vi.advanceTimersByTime(3000));
    const drawer = screen.getByRole('region', { name: 'Advisory details' });
    expect(
      within(drawer).getByRole('button', { name: 'Approve' }),
    ).toBeDisabled();
    expect(
      within(drawer).getByRole('button', { name: 'Submit for revalidation' }),
    ).toBeDisabled();
    expect(within(drawer).getByText(/expired/i)).toBeVisible();
    expect(h.server.calls).toHaveLength(0);
  });
  it('records approval on the map and opens accepted scenarios from the History page', async () => {
    const h = setup();
    expect(
      screen.queryByRole('heading', { name: 'Decision history' }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      within(h.firstCard()).getByRole('button', { name: 'Approve' }),
    );
    await waitFor(() =>
      expect(
        screen.getByText(
          'Accepted scenario · decision recorded by Controller A',
        ),
      ).toBeVisible(),
    );
    expect(
      screen.getByRole('region', { name: 'Map projection' }),
    ).toBeVisible();
    expect(
      screen.queryByRole('region', { name: 'Scenario details' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('img', { name: /Baseline versus proposed/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    expect(screen.getByRole('button', { name: 'History' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'View accepted projection' }),
    );
    expect(
      screen.queryByRole('heading', { name: 'Decision history' }),
    ).not.toBeInTheDocument();
    fireEvent.keyDown(
      screen.getByRole('button', { name: 'Close projection' }),
      { key: 'Escape' },
    );
    expect(
      screen.getByRole('heading', { name: 'Decision history' }),
    ).toHaveFocus();
    expect(window.location.hash).toBe('#/history');
  });

  it('starts honestly disconnected without synthetic observations or controls', () => {
    render(<App />);
    expect(screen.getByText('Connection unavailable')).toBeVisible();
    expect(screen.getByText('No current positions available')).toBeVisible();
    expect(
      screen.getByText('No advisories supported by current data'),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Approve' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText('DEMO101')).not.toBeInTheDocument();
    expect(screen.getByText('Weather unavailable')).toBeVisible();
  });
  it('shows source ages, evidence, ownership and the overflow queue', () => {
    const h = setup();
    expect(screen.getByText('Demo mode')).toBeVisible();
    expect(
      screen.queryByText('Dashboard synchronized'),
    ).not.toBeInTheDocument();
    expect(screen.queryByText('Live connection')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('Demo mode'));
    expect(
      screen.getByText(/Aircraft, weather and advisories are simulated/),
    ).toBeVisible();
    expect(screen.getByText('1 additional advisories')).toBeVisible();
    expect(within(h.firstCard()).getByText('Flow coordinator')).toBeVisible();
    fireEvent.click(
      within(h.firstCard()).getByRole('button', { name: 'Review details' }),
    );
    expect(
      within(
        screen.getByRole('region', { name: 'Advisory details' }),
      ).getByText(/test-evidence-1/),
    ).toBeVisible();
    expect(
      within(
        screen.getByRole('region', { name: 'Advisory details' }),
      ).getByText(/Record decision and select projected scenario/),
    ).toBeVisible();
  });
  it('supports preview focus, time selection, comparison and Escape', () => {
    const h = setup();
    const preview = within(h.firstCard()).getByRole('button', {
      name: 'Preview',
    });
    preview.focus();
    fireEvent.click(preview);
    expect(
      screen.getByRole('button', { name: 'Close projection' }),
    ).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: 'Scenario details' }));
    expect(screen.getByText(/Projected scenario from snapshot/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '+15 min' }));
    expect(screen.getByRole('button', { name: '+15 min' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(
      screen.getByRole('cell', { name: '231 aircraft-min' }),
    ).toBeVisible();
    fireEvent.keyDown(
      screen.getByRole('button', { name: 'Close projection' }),
      { key: 'Escape' },
    );
    expect(
      screen.queryByRole('region', { name: 'Map projection' }),
    ).not.toBeInTheDocument();
    expect(
      within(h.firstCard()).getByRole('button', { name: 'Preview' }),
    ).toHaveFocus();
  });
  it('retains a fixed projection while current observations refresh', () => {
    const h = setup();
    fireEvent.click(
      within(h.firstCard()).getByRole('button', { name: 'Preview' }),
    );
    const before = structuredClone(h.server.getData().projections);
    act(() =>
      h.server.setData({
        ...h.server.getData(),
        nearbyCount: 4,
        aircraft: [
          ...h.server.getData().aircraft,
          {
            ...h.server.getData().aircraft[0],
            id: 'new-test',
            label: 'TEST404',
          },
        ],
      }),
    );
    expect(
      screen.getByRole('region', { name: /4 recent observed aircraft/ }),
    ).toBeVisible();
    expect(h.server.getData().projections).toEqual(before);
    fireEvent.click(screen.getByRole('button', { name: 'Scenario details' }));
    expect(screen.getByText(/test-snapshot-1. Comparison/)).toBeVisible();
    expect(h.server.calls).toEqual([]);
  });
  it('shows unavailable metrics instead of zero for a density-only projection', () => {
    setup();
    fireEvent.click(
      within(
        screen.getByRole('article', {
          name: 'Monitor nearby traffic pressure',
        }),
      ).getByRole('button', { name: 'Preview' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Scenario details' }));
    const comparison = screen.getByRole('table', {
      name: 'Scenario comparison at now',
    });
    expect(
      within(comparison).getAllByRole('cell', {
        name: 'Not estimable with available data',
      }),
    ).toHaveLength(8);
  });
  it('blocks a read-only session and disables actions through reconnect', () => {
    const viewer = setup('test-viewer');
    expect(
      within(viewer.firstCard()).getByRole('button', { name: 'Approve' }),
    ).toBeDisabled();
    viewer.unmount();
    const h = setup();
    act(() => h.server.disconnect());
    expect(
      within(h.firstCard()).getByRole('button', { name: 'Approve' }),
    ).toBeDisabled();
    act(() => h.server.synchronizing());
    expect(
      within(h.firstCard()).getByRole('button', { name: 'Approve' }),
    ).toBeDisabled();
    act(() => h.server.synchronize());
    expect(
      within(h.firstCard()).getByRole('button', { name: 'Approve' }),
    ).toBeEnabled();
  });
  it('expires an advisory during review and retains its projection and history state', () => {
    vi.useFakeTimers();
    const h = setup();
    const data = h.server.getData();
    act(() =>
      h.server.setData({
        ...data,
        advisories: data.advisories.map((a) => ({
          ...a,
          expiresAtUtc: new Date(Date.now() + 2000).toISOString(),
        })),
      }),
    );
    fireEvent.click(
      within(h.firstCard()).getByRole('button', { name: 'Preview' }),
    );
    act(() => vi.advanceTimersByTime(3000));
    expect(
      screen.queryByRole('article', {
        name: 'Review departure release window',
      }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('region', { name: 'Map projection' }),
    ).toBeVisible();
    fireEvent.keyDown(
      screen.getByRole('button', { name: 'Close projection' }),
      { key: 'Escape' },
    );
    expect(screen.getByRole('heading', { name: 'Advisories' })).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    expect(screen.getAllByText('expired')).toHaveLength(4);
    expect(
      screen.getByRole('heading', { name: 'Decision history' }),
    ).toHaveFocus();
    expect(h.server.calls).toEqual([]);
  });
  it('submits bounded edits, waits for revalidation, then approves the new version', async () => {
    const h = setup();
    fireEvent.click(
      within(h.firstCard()).getByRole('button', { name: 'Edit' }),
    );
    const submit = screen.getByRole('button', {
      name: 'Submit for revalidation',
    });
    expect(submit).toBeDisabled();
    fireEvent.change(
      screen.getByRole('spinbutton', { name: /Aggregate release rate/ }),
      { target: { value: '8' } },
    );
    fireEvent.change(screen.getByRole('textbox', { name: 'Rationale' }), {
      target: { value: 'Review total waiting tradeoff.' },
    });
    expect(submit).toBeDisabled();
    fireEvent.change(
      screen.getByRole('spinbutton', { name: /Aggregate release rate/ }),
      { target: { value: '1.5' } },
    );
    fireEvent.click(submit);
    await waitFor(() =>
      expect(
        screen.getByText('Revised draft submitted. Awaiting revalidation.'),
      ).toBeVisible(),
    );
    expect(
      h.server
        .getData()
        .advisories.find((a) => a.id === 'test-advisory-1' && a.version === 2)
        ?.status,
    ).toBe('revalidating');
    fireEvent.click(
      screen.getByRole('button', { name: 'Close advisory details' }),
    );
    expect(screen.getByText(/v2 awaiting revalidation/)).toBeVisible();
    act(() => h.server.revalidate());
    fireEvent.click(
      within(h.firstCard()).getByRole('button', { name: 'Approve' }),
    );
    await waitFor(() =>
      expect(
        screen.getByText(
          'Accepted scenario · decision recorded by Controller A',
        ),
      ).toBeVisible(),
    );
    expect(
      screen.getByText('Accepted scenario · decision recorded by Controller A'),
    ).toBeVisible();
    expect(h.server.calls[1].version).toBe(2);
    expect(h.server.calls[1].kind).toBe('decision');
  });
  it('surfaces a server rejection and permits retry without invented acceptance', async () => {
    const server = createTestServer();
    const transport = server.transport('test-controller-a');
    const command = vi
      .fn(transport.command)
      .mockRejectedValueOnce(
        new Error('Server rejected material evidence change.'),
      );
    const client = createDashboardClient({ ...transport, command }, 'TEST');
    render(<App client={client} />);
    const card = screen.getByRole('article', {
      name: 'Review departure release window',
    });
    fireEvent.click(within(card).getByRole('button', { name: 'Approve' }));
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'material evidence change',
      ),
    );
    expect(server.getData().history).toEqual([]);
    fireEvent.click(within(card).getByRole('button', { name: 'Approve' }));
    await waitFor(() =>
      expect(
        screen.getByText(
          'Accepted scenario · decision recorded by Controller A',
        ),
      ).toBeVisible(),
    );
    expect(command.mock.calls[0][0].requestId).toBe(
      command.mock.calls[1][0].requestId,
    );
  });
  it('synchronizes approve and ignore across two TEST controller sessions without altering observations', async () => {
    const server = createTestServer();
    const before = structuredClone(server.getData().aircraft);
    const a = render(
      <App
        client={createDashboardClient(
          server.transport('test-controller-a'),
          'TEST',
        )}
      />,
    );
    const b = render(
      <App
        client={createDashboardClient(
          server.transport('test-controller-b'),
          'TEST',
        )}
      />,
    );
    fireEvent.click(
      within(
        within(a.container).getByRole('article', {
          name: 'Review departure release window',
        }),
      ).getByRole('button', { name: 'Approve' }),
    );
    fireEvent.click(
      within(b.container).getByRole('button', { name: 'History' }),
    );
    await waitFor(() =>
      expect(
        within(b.container).getByRole('button', {
          name: 'View accepted projection',
        }),
      ).toBeVisible(),
    );
    fireEvent.click(
      within(b.container).getByRole('button', {
        name: 'View accepted projection',
      }),
    );
    expect(
      within(b.container).getByText(
        'Accepted scenario · decision recorded by Controller A',
      ),
    ).toBeVisible();
    fireEvent.click(
      within(b.container).getByRole('button', { name: 'Close projection' }),
    );
    fireEvent.click(
      within(b.container).getByRole('button', { name: 'Overview' }),
    );
    fireEvent.click(
      within(
        within(b.container).getByRole('article', {
          name: 'Monitor nearby traffic pressure',
        }),
      ).getByRole('button', { name: 'Ignore' }),
    );
    fireEvent.click(
      within(a.container).getByRole('button', { name: 'History' }),
    );
    await waitFor(() =>
      expect(within(a.container).getByText('ignored')).toBeVisible(),
    );
    expect(server.getData().aircraft).toEqual(before);
  });
  it('marks stale positions and excludes expired or missing coordinates', () => {
    const h = setup();
    const data = h.server.getData();
    act(() =>
      h.server.setData({
        ...data,
        aircraft: [
          {
            ...data.aircraft[0],
            label: 'STALE-TEST',
            observedAtUtc: new Date(Date.now() - 80_000).toISOString(),
          },
          {
            ...data.aircraft[1],
            observedAtUtc: new Date(Date.now() - 130_000).toISOString(),
          },
          { ...data.aircraft[2], latitudeDeg: NaN },
        ],
        nearbyCount: null,
      }),
    );
    expect(
      screen.getByRole('region', { name: /1 recent observed aircraft/ }),
    ).toBeVisible();
    fireEvent.click(screen.getByText('Aircraft', { exact: true }));
    expect(screen.getByText('STALE-TEST · stale')).toBeVisible();
    expect(screen.queryByText('DEMO202')).not.toBeInTheDocument();
    expect(screen.getByText('Not estimable with available data')).toBeVisible();
  });
  it('treats source text as text and rejects non-web attribution links', () => {
    const h = setup();
    const data = h.server.getData();
    act(() =>
      h.server.setData({
        ...data,
        sources: [
          {
            ...data.sources[0],
            detail: '<script>approve()</script>',
            attribution: 'Unsafe link',
            attributionUrl: 'javascript:approve()',
          },
        ],
      }),
    );
    fireEvent.click(screen.getByText('Aviation weather'));
    expect(screen.getByText('<script>approve()</script>')).toBeVisible();
    expect(
      screen.queryByRole('link', { name: 'Unsafe link' }),
    ).not.toBeInTheDocument();
    expect(h.server.calls).toHaveLength(0);
  });
  it('renders unknown values and feed health in a missing-data scenario', () => {
    const server = createTestServer({
      ...scenarioData(),
      aircraft: [],
      nearbyCount: null,
      advisories: [],
      projections: [],
      weather: null,
    });
    render(
      <App
        client={createDashboardClient(server.transport('test-viewer'), 'TEST')}
      />,
    );
    expect(screen.getByText('—')).toBeVisible();
    expect(screen.getByText('Not estimable with available data')).toBeVisible();
    expect(
      screen.getByText('No advisories supported by current data'),
    ).toBeVisible();
  });
});
