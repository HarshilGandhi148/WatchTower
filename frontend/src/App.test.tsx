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

// WebGL rendering is tested at the map boundary. These tests exercise the
// complete review flow with deterministic map state and selection controls.
vi.mock('./components/dashboard/TrafficMap', () => ({
  TrafficMap: ({
    aircraft,
    children,
    mapProjection,
    onSelectProjectedAircraft,
  }: {
    aircraft: readonly unknown[];
    children?: ReactNode;
    mapProjection?: {
      branch?: string;
      minute: number;
      selectedAircraftId?: string | null;
      projection?: {
        aircraftPaths?: {
          trajectories: readonly { aircraftId: string; label: string }[];
        };
      };
    } | null;
    onSelectProjectedAircraft?: (id: string) => void;
  }) => (
    <div
      role="region"
      aria-label={`DTW geographic map. ${aircraft.length} recent observed aircraft.`}
      data-branch={mapProjection?.branch}
      data-minute={mapProjection?.minute}
      data-selected={mapProjection?.selectedAircraftId ?? ''}
    >
      {!aircraft.length && <strong>No current positions available</strong>}
      {mapProjection?.projection?.aircraftPaths?.trajectories.map((path) => (
        <button
          key={path.aircraftId}
          onClick={() => onSelectProjectedAircraft?.(path.aircraftId)}
        >
          Projected {path.label}
        </button>
      ))}
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
const openReview = (
  title = 'Review departure release window',
  scope: Pick<typeof screen, 'getByRole'> = screen,
) => {
  const list = scope.getByRole('region', { name: 'Advisory list' });
  fireEvent.click(
    within(within(list).getByRole('article', { name: title })).getByRole(
      'button',
      { name: 'Preview' },
    ),
  );
  return scope.getByRole('region', { name: 'Scenario review' });
};
const closeProjection = (scope: Pick<typeof screen, 'getByRole'> = screen) =>
  fireEvent.click(scope.getByRole('button', { name: 'Close projection' }));
const acceptedText = 'Accepted scenario · decision recorded by Controller A';

describe('controller dashboard', () => {
  it('starts disconnected without synthetic observations or decisions', () => {
    render(<App />);
    expect(screen.getByText('Connection unavailable')).toBeVisible();
    expect(screen.getByText('No current positions available')).toBeVisible();
    expect(
      screen.getByText('No advisories supported by current data'),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Approve scenario' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText('DEMO101')).not.toBeInTheDocument();
    expect(screen.getByText('Weather unavailable')).toBeVisible();
  });
  it('shows a quiet prioritized queue and keeps decisions inside review', () => {
    const h = setup();
    expect(screen.getByText('Demo mode')).toBeVisible();
    expect(screen.queryByText('Live connection')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('Demo mode'));
    expect(
      screen.getByText(/Aircraft, weather and advisories are simulated/),
    ).toBeVisible();
    expect(screen.getByText('1 additional advisories')).toBeVisible();
    expect(
      within(h.firstCard()).queryByRole('button', {
        name: /Approve|Edit|Ignore/,
      }),
    ).not.toBeInTheDocument();
    const review = openReview();
    expect(within(review).getByText('Flow coordinator')).toBeVisible();
    expect(
      within(review).getByRole('button', { name: 'Approve scenario' }),
    ).toBeEnabled();
    expect(
      within(review).getByText(/Record decision and select projected scenario/),
    ).toBeVisible();
  });
  it('opens review beside the mounted map and restores focus when closing', () => {
    const h = setup();
    const map = screen.getByRole('region', {
      name: /3 recent observed aircraft/,
    });
    const trigger = within(h.firstCard()).getByRole('button', {
      name: 'Preview',
    });
    const queue = h.firstCard().closest('.advisory-panel');
    trigger.focus();
    fireEvent.click(trigger);
    expect(queue).toHaveAttribute('hidden');
    expect(
      screen.getByRole('region', { name: 'Map projection' }),
    ).toBeVisible();
    expect(map.closest('.live-panel')).not.toHaveAttribute('inert');
    expect(
      screen.getByRole('button', { name: 'Close scenario details' }),
    ).toHaveFocus();
    fireEvent.keyDown(
      screen.getByRole('button', { name: 'Close scenario details' }),
      { key: 'Escape' },
    );
    expect(queue).not.toHaveAttribute('hidden');
    expect(trigger).toHaveFocus();
    expect(
      screen.getByRole('region', { name: /3 recent observed aircraft/ }),
    ).toBe(map);
    const reopen = screen.getByRole('button', { name: 'Open scenario review' });
    reopen.focus();
    fireEvent.click(reopen);
    expect(
      screen.getByRole('region', { name: 'Scenario review' }),
    ).toBeVisible();
    closeProjection();
    expect(trigger).toHaveFocus();
    expect(h.server.calls).toEqual([]);
  });
  it('offers keyboard-operated evidence tabs with exact timestamps and assumptions', () => {
    setup();
    const review = openReview();
    const overview = within(review).getByRole('tab', { name: 'Overview' });
    overview.focus();
    fireEvent.keyDown(overview, { key: 'ArrowRight' });
    expect(within(review).getByRole('tab', { name: 'Evidence' })).toHaveFocus();
    expect(
      within(review).getByRole('tab', { name: 'Evidence' }),
    ).toHaveAttribute('aria-selected', 'true');
    expect(within(review).getByText(/test-evidence-1/)).toBeVisible();
    expect(
      within(review).getByText(/Hand-authored MHacks demo paths/),
    ).toBeVisible();
    fireEvent.keyDown(within(review).getByRole('tab', { name: 'Evidence' }), {
      key: 'Home',
    });
    expect(overview).toHaveFocus();
    expect(
      within(review).getByRole('heading', { name: '15-minute outcomes' }),
    ).toBeVisible();
  });
  it('shows 15-minute outcomes separately from exact sampled timeline values', () => {
    setup();
    const review = openReview();
    const waiting = within(review).getByRole('article', {
      name: 'Total waiting at +15 minutes',
    });
    expect(within(waiting).getByText('218')).toBeVisible();
    expect(within(waiting).getByText('231')).toBeVisible();
    expect(within(waiting).getByText('+13 aircraft-min')).toHaveClass(
      'adverse-change',
    );
    fireEvent.change(screen.getByRole('slider', { name: 'Projected time' }), {
      target: { value: '7.5' },
    });
    fireEvent.click(within(review).getByText('Sampled comparison · +5 min'));
    expect(
      within(review).getByRole('table', {
        name: 'Scenario comparison at +5 minutes',
      }),
    ).toBeVisible();
    expect(
      within(review).getByRole('cell', { name: '55 aircraft-min' }),
    ).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '+15 min' }));
    expect(
      within(review).getByRole('table', {
        name: 'Scenario comparison at +15 minutes',
      }),
    ).toBeVisible();
    expect(
      within(review).getByRole('cell', { name: '231 aircraft-min' }),
    ).toBeVisible();
    expect(
      screen.queryByRole('img', { name: /Baseline versus proposed/ }),
    ).not.toBeInTheDocument();
  });
  it('switches branches and selects aircraft without resetting time or observed data', () => {
    const h = setup();
    const before = structuredClone(h.server.getData().aircraft);
    const review = openReview();
    const map = screen.getByRole('region', {
      name: /3 recent observed aircraft/,
    });
    fireEvent.click(screen.getByRole('button', { name: '+10 min' }));
    fireEvent.click(
      within(
        screen.getByRole('group', { name: 'Displayed scenario' }),
      ).getByRole('button', { name: 'Baseline' }),
    );
    expect(map).toHaveAttribute('data-branch', 'baseline');
    expect(map).toHaveAttribute('data-minute', '10');
    fireEvent.click(
      within(review).getByRole('button', { name: 'Select DEMO101' }),
    );
    expect(map).toHaveAttribute('data-selected', 'test-plane-1');
    fireEvent.click(
      within(map).getByRole('button', { name: 'Projected DEMO202' }),
    );
    expect(
      within(review).getByRole('button', { name: 'Select DEMO202' }),
    ).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(
      within(review).getByRole('button', { name: 'Select DEMO202' }),
    );
    expect(map).toHaveAttribute('data-selected', '');
    expect(h.server.getData().aircraft).toEqual(before);
    expect(h.server.calls).toEqual([]);
  });
  it('retains the fixed snapshot while current observations refresh', () => {
    const h = setup();
    const review = openReview();
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
    fireEvent.click(within(review).getByRole('tab', { name: 'Evidence' }));
    expect(within(review).getByText(/Snapshot test-snapshot-1/)).toBeVisible();
    expect(h.server.calls).toEqual([]);
  });
  it('shows supported pressure and unavailable departure metrics in density-only previews', () => {
    setup();
    const review = openReview('Monitor nearby traffic pressure');
    expect(
      within(review).getByRole('article', {
        name: 'Traffic pressure at +15 minutes',
      }),
    ).toBeVisible();
    expect(
      within(review).getByText(
        'Departure metrics are not estimable with available data.',
      ),
    ).toBeVisible();
    fireEvent.click(within(review).getByText('Sampled comparison · Now'));
    const comparison = within(review).getByRole('table', {
      name: 'Scenario comparison at now',
    });
    expect(
      within(comparison).getAllByRole('cell', {
        name: 'Not estimable with available data',
      }),
    ).toHaveLength(8);
  });
  it('blocks read-only decisions and waits for authoritative reconnect state', () => {
    const viewer = setup('test-viewer');
    openReview();
    expect(
      screen.getByRole('button', { name: 'Approve scenario' }),
    ).toBeDisabled();
    viewer.unmount();
    const h = setup();
    openReview();
    act(() => h.server.disconnect());
    expect(
      screen.getByRole('button', { name: 'Approve scenario' }),
    ).toBeDisabled();
    act(() => h.server.synchronizing());
    expect(
      screen.getByRole('button', { name: 'Approve scenario' }),
    ).toBeDisabled();
    act(() => h.server.synchronize());
    expect(
      screen.getByRole('button', { name: 'Approve scenario' }),
    ).toBeEnabled();
  });
  it('retains an expired version and disables decisions while review and playback remain available', () => {
    vi.useFakeTimers();
    const h = setup();
    act(() =>
      h.server.setData({
        ...h.server.getData(),
        advisories: h.server.getData().advisories.map((a) => ({
          ...a,
          expiresAtUtc: new Date(Date.now() + 2000).toISOString(),
        })),
      }),
    );
    const review = openReview();
    fireEvent.click(within(review).getByRole('button', { name: 'Edit' }));
    act(() => vi.advanceTimersByTime(3000));
    expect(
      within(review).getByRole('button', { name: 'Approve scenario' }),
    ).toBeDisabled();
    expect(
      within(review).getByRole('button', { name: 'Submit for revalidation' }),
    ).toBeDisabled();
    expect(within(review).getByText(/This advisory has expired/)).toBeVisible();
    expect(
      screen.getByRole('region', { name: 'Map projection' }),
    ).toBeVisible();
    closeProjection();
    expect(screen.getByRole('heading', { name: 'Advisories' })).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    expect(screen.getAllByText('expired')).toHaveLength(4);
    expect(h.server.calls).toHaveLength(0);
  });
  it('does not silently replace a reviewed version when it is superseded', () => {
    const h = setup();
    const review = openReview();
    const data = h.server.getData();
    const old = data.advisories[0];
    act(() =>
      h.server.setData({
        ...data,
        advisories: [
          ...data.advisories.map((a) =>
            a.id === old.id ? ({ ...a, status: 'superseded' } as const) : a,
          ),
          { ...old, version: 2 },
        ],
      }),
    );
    expect(within(review).getByText(/Scenario review · v1/)).toBeVisible();
    expect(
      within(review).getByRole('button', { name: 'Approve scenario' }),
    ).toBeDisabled();
    expect(
      within(review).getByText('This version is superseded.'),
    ).toBeVisible();
    closeProjection();
    expect(within(h.firstCard()).getByText('v2')).toBeVisible();
    expect(h.server.calls).toHaveLength(0);
  });
  it('submits bounded edits, waits for validation, then approves the revised version', async () => {
    const h = setup();
    const review = openReview();
    fireEvent.click(within(review).getByRole('button', { name: 'Edit' }));
    const input = screen.getByRole('spinbutton', {
      name: /Aggregate release rate/,
    });
    expect(input).toHaveFocus();
    const submit = screen.getByRole('button', {
      name: 'Submit for revalidation',
    });
    expect(submit).toBeDisabled();
    fireEvent.change(input, { target: { value: '8' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Rationale' }), {
      target: { value: 'Review total waiting tradeoff.' },
    });
    expect(submit).toBeDisabled();
    fireEvent.change(input, { target: { value: '1.5' } });
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
    expect(h.server.getData().history[0].status).toBe('edited');
    closeProjection();
    expect(screen.getByText(/v2 awaiting revalidation/)).toBeVisible();
    act(() => h.server.revalidate());
    openReview();
    fireEvent.click(screen.getByRole('button', { name: 'Approve scenario' }));
    await waitFor(() => expect(screen.getByText(acceptedText)).toBeVisible());
    expect(h.server.calls[1].version).toBe(2);
    expect(h.server.calls[1].kind).toBe('decision');
  });
  it('waits for acknowledgement and prevents repeated approval submissions', async () => {
    const server = createTestServer();
    const before = structuredClone(server.getData().aircraft);
    const transport = server.transport('test-controller-a');
    let finish!: () => void;
    const gate = new Promise<void>((resolve) => {
      finish = resolve;
    });
    const command = vi.fn(
      async (input: Parameters<typeof transport.command>[0]) => {
        await gate;
        return transport.command(input);
      },
    );
    render(
      <App client={createDashboardClient({ ...transport, command }, 'TEST')} />,
    );
    openReview();
    const approve = screen.getByRole('button', { name: 'Approve scenario' });
    fireEvent.click(approve);
    fireEvent.click(approve);
    await waitFor(() => expect(command).toHaveBeenCalledOnce());
    expect(approve).toBeDisabled();
    expect(screen.getByText('Submitting request…')).toBeVisible();
    expect(screen.queryByText(acceptedText)).not.toBeInTheDocument();
    expect(server.getData().history).toEqual([]);
    await act(async () => {
      finish();
      await gate;
    });
    await waitFor(() => expect(screen.getByText(acceptedText)).toBeVisible());
    expect(server.calls).toHaveLength(1);
    expect(server.getData().aircraft).toEqual(before);
  });
  it('starts proposed playback at now only after the approval acknowledgement', async () => {
    const server = createTestServer();
    const transport = server.transport('test-controller-a');
    let finish!: () => void;
    const gate = new Promise<void>((resolve) => {
      finish = resolve;
    });
    const command = vi.fn(
      async (input: Parameters<typeof transport.command>[0]) => {
        const acknowledgement = await transport.command(input);
        await gate;
        return acknowledgement;
      },
    );
    render(
      <App client={createDashboardClient({ ...transport, command }, 'TEST')} />,
    );
    openReview();
    fireEvent.click(screen.getByRole('button', { name: '+10 min' }));
    fireEvent.click(
      within(
        screen.getByRole('group', { name: 'Displayed scenario' }),
      ).getByRole('button', { name: 'Baseline' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Approve scenario' }));
    await waitFor(() => expect(server.getData().history).toHaveLength(1));
    expect(
      screen.getByRole('button', { name: 'Play projection' }),
    ).toBeEnabled();
    expect(screen.getByText('Submitting request…')).toBeVisible();
    await act(async () => {
      finish();
      await gate;
    });
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Pause projection' }),
      ).toBeEnabled(),
    );
    const map = screen.getByRole('region', {
      name: /3 recent observed aircraft/,
    });
    expect(map).toHaveAttribute('data-branch', 'proposed');
    expect(Number(map.getAttribute('data-minute'))).toBeLessThan(1);
  });
  it('surfaces rejected requests and retries with the same stable request ID', async () => {
    const server = createTestServer();
    const transport = server.transport('test-controller-a');
    const command = vi
      .fn(transport.command)
      .mockRejectedValueOnce(
        new Error('Server rejected material evidence change.'),
      );
    render(
      <App client={createDashboardClient({ ...transport, command }, 'TEST')} />,
    );
    openReview();
    fireEvent.click(screen.getByRole('button', { name: 'Approve scenario' }));
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'material evidence change',
      ),
    );
    expect(server.getData().history).toEqual([]);
    expect(screen.queryByText(acceptedText)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Approve scenario' }));
    await waitFor(() => expect(screen.getByText(acceptedText)).toBeVisible());
    expect(command.mock.calls[0][0].requestId).toBe(
      command.mock.calls[1][0].requestId,
    );
  });
  it('reopens accepted history as read-only review and returns to its own page', async () => {
    setup();
    openReview();
    fireEvent.click(screen.getByRole('button', { name: 'Approve scenario' }));
    await waitFor(() => expect(screen.getByText(acceptedText)).toBeVisible());
    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    expect(
      screen.getByRole('heading', { name: 'Decision history' }),
    ).toHaveFocus();
    expect(window.location.hash).toBe('#/history');
    fireEvent.click(
      screen.getByRole('button', { name: 'View accepted projection' }),
    );
    const review = screen.getByRole('region', { name: 'Scenario review' });
    expect(within(review).getByText(acceptedText)).toBeVisible();
    expect(
      within(review).queryByRole('button', { name: 'Approve scenario' }),
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
  it('supports direct History links and browser navigation while retaining the map', () => {
    window.history.replaceState(null, '', '#/history');
    const h = setup();
    const before = structuredClone(h.server.getData().aircraft);
    expect(
      screen.getByRole('heading', { name: 'Decision history' }),
    ).toHaveFocus();
    act(() => {
      window.history.replaceState(null, '', '#/overview');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    const map = screen.getByRole('region', {
      name: /3 recent observed aircraft/,
    });
    act(() => {
      window.history.replaceState(null, '', '#/history');
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    });
    fireEvent.click(screen.getByRole('button', { name: 'Overview' }));
    expect(
      screen.getByRole('region', { name: /3 recent observed aircraft/ }),
    ).toBe(map);
    expect(
      screen.getByRole('heading', { name: 'Traffic overview' }),
    ).toHaveFocus();
    expect(h.server.getData().aircraft).toEqual(before);
    expect(h.server.calls).toEqual([]);
  });
  it('synchronizes decisions across two TEST identities without changing observations', async () => {
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
    const one = within(a.container);
    const two = within(b.container);
    openReview(undefined, one);
    fireEvent.click(one.getByRole('button', { name: 'Approve scenario' }));
    fireEvent.click(two.getByRole('button', { name: 'History' }));
    await waitFor(() =>
      expect(
        two.getByRole('button', { name: 'View accepted projection' }),
      ).toBeVisible(),
    );
    fireEvent.click(
      two.getByRole('button', { name: 'View accepted projection' }),
    );
    expect(two.getByText(acceptedText)).toBeVisible();
    closeProjection(two);
    fireEvent.click(two.getByRole('button', { name: 'Overview' }));
    openReview('Monitor nearby traffic pressure', two);
    fireEvent.click(two.getByRole('button', { name: 'Ignore' }));
    fireEvent.click(one.getByRole('button', { name: 'History' }));
    await waitFor(() => expect(one.getByText('ignored')).toBeVisible());
    expect(server.getData().aircraft).toEqual(before);
  });
  it('keeps missing or mismatched projections unavailable and prevents approval', () => {
    const h = setup();
    const data = h.server.getData();
    act(() =>
      h.server.setData({
        ...data,
        projections: data.projections.map((projection) => ({
          ...projection,
          snapshotId: 'wrong',
        })),
      }),
    );
    const review = openReview();
    expect(within(review).getByRole('alert')).toHaveTextContent(
      'does not match',
    );
    expect(
      within(review).queryByRole('heading', { name: '15-minute outcomes' }),
    ).not.toBeInTheDocument();
    expect(
      within(review).getByRole('button', { name: 'Approve scenario' }),
    ).toBeDisabled();
    expect(
      within(review).getByRole('button', { name: 'Ignore' }),
    ).toBeEnabled();
    expect(h.server.calls).toEqual([]);
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
  it('escapes source text and rejects non-web attribution links', () => {
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
  it('renders unavailable feed health, empty queues and unknown values honestly', () => {
    const data = scenarioData();
    const server = createTestServer({
      ...data,
      aircraft: [],
      nearbyCount: null,
      advisories: [],
      projections: [],
      weather: null,
      sources: data.sources.map((source) => ({
        ...source,
        status: 'unavailable',
      })),
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
    fireEvent.click(screen.getByText('Aviation weather'));
    expect(screen.getByText('Aviation weather · unavailable')).toBeVisible();
  });
});
