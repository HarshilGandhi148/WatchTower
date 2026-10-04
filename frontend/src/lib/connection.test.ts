import { describe, expect, it, vi } from 'vitest';
import { createDashboardClient, type DashboardTransport } from './connection';
import { scenarioData } from '../test/scenarios';

function harness() {
  const events: Parameters<DashboardTransport['connect']>[0][] = [];
  const cleanup = vi.fn();
  const command = vi.fn<DashboardTransport['command']>(async (input) => ({
    requestId: input.requestId,
    message: 'Recorded.',
  }));
  const transport: DashboardTransport = {
    connect(event) {
      events.push(event);
      return cleanup;
    },
    command,
  };
  const client = createDashboardClient(transport, 'TEST');
  const stop = client.start();
  const data = scenarioData();
  events[0].ready({
    ...data,
    session: {
      identity: 'controller',
      displayName: 'Controller',
      decidableAdvisoryIds: data.advisories.map((a) => a.id),
    },
  });
  const input = {
    kind: 'decision' as const,
    advisoryId: data.advisories[0].id,
    version: 1,
    snapshotId: data.advisories[0].snapshotId,
    decision: 'approve' as const,
  };
  return { client, transport, events, cleanup, command, stop, data, input };
}
describe('subscription and request lifecycle', () => {
  it('has no live fallback without reviewed bindings', async () => {
    const client = createDashboardClient();
    client.start();
    expect(client.getSnapshot().connection).toBe('unconfigured');
    expect(client.getSnapshot().aircraft).toEqual([]);
    await expect(
      client.submit({
        kind: 'decision',
        advisoryId: 'x',
        version: 1,
        snapshotId: 'x',
        decision: 'approve',
      }),
    ).rejects.toThrow('unavailable');
  });
  it('disables decisions through disconnect and resynchronization', async () => {
    const h = harness();
    h.events[0].disconnected('Lost connection');
    await expect(h.client.submit(h.input)).rejects.toThrow('synchronized');
    expect(h.client.getSnapshot().aircraft).toEqual(h.data.aircraft);
    h.events[0].synchronizing();
    await expect(h.client.submit(h.input)).rejects.toThrow('synchronized');
    h.events[0].ready({ ...h.data, session: null });
    await expect(h.client.submit(h.input)).rejects.toThrow('session');
    expect(h.command).not.toHaveBeenCalled();
  });
  it('deduplicates repeated clicks and blocks a conflicting in-flight action', async () => {
    const h = harness();
    const first = h.client.submit(h.input);
    const repeated = h.client.submit(h.input);
    expect(first).toBe(repeated);
    await expect(
      h.client.submit({ ...h.input, decision: 'ignore' }),
    ).rejects.toThrow('already pending');
    await first;
    await h.client.submit(h.input);
    expect(h.command).toHaveBeenCalledTimes(1);
    expect(h.client.getSnapshot().history).toEqual([]); // No optimistic acceptance.
  });
  it('reuses the same request ID after a lost acknowledgement', async () => {
    const h = harness();
    h.command.mockRejectedValueOnce(new Error('Timeout'));
    await expect(h.client.submit(h.input)).rejects.toThrow('Timeout');
    await h.client.submit(h.input);
    expect(h.command.mock.calls[0][0].requestId).toBe(
      h.command.mock.calls[1][0].requestId,
    );
  });
  it('rejects old versions, changed snapshots, expired versions and unassigned sessions', async () => {
    const h = harness();
    await expect(
      h.client.submit({ ...h.input, snapshotId: 'other' }),
    ).rejects.toThrow('changed');
    h.events[0].ready({
      ...h.client.getSnapshot(),
      advisories: h.data.advisories.map((a) => ({ ...a, version: 2 })),
    });
    await expect(h.client.submit(h.input)).rejects.toThrow('changed');
    h.events[0].ready({
      ...h.client.getSnapshot(),
      advisories: h.data.advisories.map((a) => ({
        ...a,
        expiresAtUtc: new Date(Date.now() - 1).toISOString(),
      })),
    });
    await expect(h.client.submit(h.input)).rejects.toThrow('expired');
    h.events[0].ready({
      ...h.data,
      session: {
        identity: 'viewer',
        displayName: 'Viewer',
        decidableAdvisoryIds: [],
      },
    });
    await expect(h.client.submit(h.input)).rejects.toThrow(
      'another controller',
    );
    expect(h.command).not.toHaveBeenCalled();
  });
  it('rejects out-of-bounds edits and extra parameters', async () => {
    const h = harness();
    const edit = {
      kind: 'edit' as const,
      advisoryId: h.input.advisoryId,
      version: 1,
      snapshotId: h.input.snapshotId,
      rationale: 'Reason',
      parameters: { releaseRate: 9 },
    };
    await expect(h.client.submit(edit)).rejects.toThrow('bounds');
    await expect(
      h.client.submit({
        ...edit,
        parameters: { releaseRate: 2, capacity: 10 },
      }),
    ).rejects.toThrow('bounds');
    await expect(
      h.client.submit({
        ...edit,
        parameters: { releaseRate: 2 },
        rationale: ' ',
      }),
    ).rejects.toThrow('bounds');
    expect(h.command).not.toHaveBeenCalled();
  });
  it('allows only one accepted decision when two TEST controllers race', async () => {
    const { createTestServer } = await import('../test/TestServer');
    const server = createTestServer();
    const a = createDashboardClient(
      server.transport('test-controller-a'),
      'TEST',
    );
    const b = createDashboardClient(
      server.transport('test-controller-b'),
      'TEST',
    );
    a.start();
    b.start();
    const advisory = server.getData().advisories[0];
    const base = {
      kind: 'decision' as const,
      advisoryId: advisory.id,
      version: advisory.version,
      snapshotId: advisory.snapshotId,
    };
    const results = await Promise.allSettled([
      a.submit({ ...base, decision: 'approve' }),
      b.submit({ ...base, decision: 'ignore' }),
    ]);
    expect(
      results.filter((result) => result.status === 'fulfilled'),
    ).toHaveLength(1);
    expect(server.getData().history).toHaveLength(1);
    expect(a.getSnapshot().history).toEqual(b.getSnapshot().history);
  });
  it('cleans subscriptions up and ignores callbacks from disposed connections', () => {
    const h = harness();
    h.stop();
    expect(h.cleanup).toHaveBeenCalledOnce();
    const nextStop = h.client.start();
    h.events[0].ready(h.data);
    expect(h.client.getSnapshot().connection).toBe('connecting');
    h.events[1].error('Server unavailable');
    expect(h.client.getSnapshot().connection).toBe('error');
    nextStop();
    expect(h.cleanup).toHaveBeenCalledTimes(2);
  });
});
