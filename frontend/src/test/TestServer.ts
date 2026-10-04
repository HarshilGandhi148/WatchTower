// Isolated TEST transport. No SDK, credentials, provider access or production imports.
import type {
  CommandAcknowledgement,
  ControllerCommand,
  DashboardData,
} from '../features/live/types';
import type { DashboardTransport } from '../lib/connection';
import { isExpired } from '../lib/time';
import { validParameter } from '../features/advisories/policy';
import { scenarioData } from './scenarios';

type Events = Parameters<DashboardTransport['connect']>[0];
export function createTestServer(initial = scenarioData()) {
  let data = initial;
  let online = true;
  const connections = new Map<Events, string>();
  const acknowledgements = new Map<string, CommandAcknowledgement>();
  const calls: ControllerCommand[] = [];
  function session(identity: string) {
    return {
      identity,
      displayName:
        identity === 'test-controller-a'
          ? 'Controller A'
          : identity === 'test-controller-b'
            ? 'Controller B'
            : 'Read-only viewer',
      decidableAdvisoryIds:
        identity === 'test-viewer' ? [] : data.advisories.map((a) => a.id),
    };
  }
  const current = (identity: string): DashboardData => ({
    ...data,
    session: session(identity),
  });
  function publish() {
    if (online)
      connections.forEach((identity, events) =>
        events.ready(current(identity)),
      );
  }
  return {
    calls,
    getData: () => data,
    setData(next: DashboardData) {
      data = next;
      publish();
    },
    disconnect() {
      online = false;
      connections.forEach((_, events) =>
        events.disconnected(
          'Connection lost. Last-known observations retained.',
        ),
      );
    },
    synchronizing() {
      online = true;
      connections.forEach((_, events) => events.synchronizing());
    },
    synchronize() {
      online = true;
      publish();
    },
    revalidate() {
      const draft = data.advisories.find((a) => a.status === 'revalidating');
      if (!draft) return;
      const oldProjection = data.projections.find(
        (p) => p.id === draft.projectionId,
      );
      const revisedId = `test-revised-${draft.id}-${draft.version}`;
      data = {
        ...data,
        advisories: data.advisories.map((a) =>
          a === draft
            ? {
                ...a,
                status: 'pending_review',
                projectionId: oldProjection ? revisedId : null,
                expiresAtUtc: new Date(Date.now() + 600_000).toISOString(),
              }
            : a,
        ),
        projections: oldProjection
          ? [
              ...data.projections,
              {
                ...oldProjection,
                id: revisedId,
                limitations: [
                  ...oldProjection.limitations,
                  'Demo revalidation uses fixed sample series; no live agent calculation.',
                ],
              },
            ]
          : data.projections,
      };
      publish();
    },
    transport(identity: string): DashboardTransport {
      return {
        connect(events) {
          connections.set(events, identity);
          if (online) events.ready(current(identity));
          else events.disconnected('Demo connection offline.');
          return () => {
            connections.delete(events);
          };
        },
        async command(command) {
          calls.push(command);
          if (!online) throw new Error('Connection lost.');
          const existing = acknowledgements.get(command.requestId);
          if (existing) return existing;
          if (
            !session(identity).decidableAdvisoryIds.includes(command.advisoryId)
          )
            throw new Error(
              'This session is not assigned to decide this advisory.',
            );
          const advisory = data.advisories
            .filter((a) => a.id === command.advisoryId)
            .sort((a, b) => b.version - a.version)[0];
          if (
            !advisory ||
            advisory.version !== command.version ||
            advisory.snapshotId !== command.snapshotId ||
            advisory.status !== 'pending_review' ||
            isExpired(advisory.expiresAtUtc)
          )
            throw new Error(
              'This advisory changed or expired. Refresh and review.',
            );
          if (command.kind === 'edit') {
            if (
              !command.rationale.trim() ||
              Object.keys(command.parameters).length !==
                advisory.parameters.length ||
              !advisory.parameters.length ||
              advisory.parameters.some(
                (p) => !validParameter(p, command.parameters[p.key]),
              )
            )
              throw new Error(
                'Invalid edit. Review parameter bounds and rationale.',
              );
            data = {
              ...data,
              advisories: [
                ...data.advisories.map((a) =>
                  a.id === advisory.id && a.version === advisory.version
                    ? { ...a, status: 'superseded' as const }
                    : a,
                ),
                {
                  ...advisory,
                  version: advisory.version + 1,
                  status: 'revalidating',
                  parameters: advisory.parameters.map((p) => ({
                    ...p,
                    value: command.parameters[p.key],
                  })),
                },
              ],
              history: [
                ...data.history,
                {
                  id: command.requestId,
                  advisoryId: advisory.id,
                  version: advisory.version,
                  title: advisory.title,
                  status: 'edited',
                  actor: session(identity).displayName,
                  atUtc: new Date().toISOString(),
                  snapshotId: advisory.snapshotId,
                  projectionId: null,
                  parameters: advisory.parameters.map((p) => ({
                    label: p.label,
                    value: command.parameters[p.key],
                    unit: p.unit,
                  })),
                },
              ],
            };
          } else {
            data = {
              ...data,
              advisories: data.advisories.map((a) =>
                a.id === advisory.id && a.version === advisory.version
                  ? {
                      ...a,
                      status:
                        command.decision === 'approve'
                          ? ('approved' as const)
                          : ('ignored' as const),
                    }
                  : a,
              ),
              history: [
                ...data.history,
                {
                  id: command.requestId,
                  advisoryId: advisory.id,
                  version: advisory.version,
                  title: advisory.title,
                  status:
                    command.decision === 'approve' ? 'approved' : 'ignored',
                  actor: session(identity).displayName,
                  atUtc: new Date().toISOString(),
                  snapshotId: advisory.snapshotId,
                  projectionId:
                    command.decision === 'approve'
                      ? advisory.projectionId
                      : null,
                  parameters: advisory.parameters.map((p) => ({
                    label: p.label,
                    value: p.value,
                    unit: p.unit,
                  })),
                },
              ],
            };
          }
          const ack = {
            requestId: command.requestId,
            message:
              command.kind === 'edit'
                ? 'Revised draft submitted. Awaiting revalidation.'
                : `${command.decision === 'approve' ? 'approval' : 'dismissal'} recorded for version ${command.version}.`,
          };
          acknowledgements.set(command.requestId, ack);
          publish();
          return ack;
        },
      };
    },
  };
}
