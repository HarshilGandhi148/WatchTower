import type {
  CommandAcknowledgement,
  ControllerCommand,
  DashboardData,
  DashboardSnapshot,
} from '../features/live/types';
import {
  decisionBlockReason,
  validParameter,
} from '../features/advisories/policy';
import { frontendConfig } from './config';

// A display adapter contract, not an imitation of the pending SpacetimeDB SDK.
// ready must supply a complete, synchronized state including assignments.
export interface DashboardTransport {
  connect(events: {
    synchronizing: () => void;
    ready: (data: DashboardData) => void;
    disconnected: (message: string) => void;
    error: (message: string) => void;
  }): () => void;
  command(command: ControllerCommand): Promise<CommandAcknowledgement>;
}
export type CommandInput =
  | Omit<Extract<ControllerCommand, { kind: 'decision' }>, 'requestId'>
  | Omit<Extract<ControllerCommand, { kind: 'edit' }>, 'requestId'>;
export interface DashboardClient {
  subscribe(listener: () => void): () => void;
  getSnapshot(): DashboardSnapshot;
  start(): () => void;
  submit(input: CommandInput): Promise<CommandAcknowledgement>;
}
export const emptyData: DashboardData = {
  refreshedAtUtc: null,
  sources: [],
  aircraft: [],
  nearbyCount: null,
  weather: null,
  eventsStatus: 'unavailable',
  events: [],
  findings: [],
  advisories: [],
  projections: [],
  history: [],
  session: null,
};
export function createDashboardClient(
  transport?: DashboardTransport,
  mode: 'LIVE' | 'TEST' = 'LIVE',
): DashboardClient {
  const configured = Boolean(
    frontendConfig.spacetimeWsUrl && frontendConfig.spacetimeDatabase,
  );
  let state: DashboardSnapshot = {
    ...emptyData,
    mode,
    connection: 'unconfigured',
    connectionMessage: configured
      ? 'Waiting for the live connection. Observations and controller access are unavailable.'
      : 'Live data is not connected. Observations and advisory decisions are unavailable.',
  };
  const listeners = new Set<() => void>();
  const ids = new Map<string, string>();
  const pending = new Map<string, Promise<CommandAcknowledgement>>();
  const acknowledged = new Map<string, CommandAcknowledgement>();
  let stop: (() => void) | undefined;
  let users = 0;
  let generation = 0;
  function update(next: DashboardSnapshot) {
    state = next;
    listeners.forEach((listener) => listener());
  }
  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    getSnapshot: () => state,
    start() {
      users += 1;
      if (users === 1 && transport) {
        const current = ++generation;
        const alive = () => generation === current && users > 0;
        update({
          ...state,
          connection: 'connecting',
          connectionMessage: 'Connecting to the dashboard…',
        });
        try {
          stop = transport.connect({
            synchronizing: () => {
              if (alive())
                update({
                  ...state,
                  connection: 'synchronizing',
                  session: null,
                  connectionMessage:
                    'Reloading current versions and controller assignments…',
                });
            },
            ready: (data) => {
              if (alive())
                update({
                  ...data,
                  mode,
                  connection: 'ready',
                  connectionMessage: 'Subscriptions synchronized.',
                });
            },
            disconnected: (message) => {
              if (alive())
                update({
                  ...state,
                  connection: 'disconnected',
                  session: null,
                  connectionMessage: message,
                });
            },
            error: (message) => {
              if (alive())
                update({
                  ...state,
                  connection: 'error',
                  session: null,
                  connectionMessage: message,
                });
            },
          });
        } catch (error) {
          update({
            ...state,
            connection: 'error',
            connectionMessage:
              error instanceof Error ? error.message : 'Connection failed.',
          });
        }
      }
      let released = false;
      return () => {
        if (released) return;
        released = true;
        users -= 1;
        if (!users && transport) {
          generation += 1;
          stop?.();
          stop = undefined;
          update({
            ...state,
            connection: 'disconnected',
            session: null,
            connectionMessage: 'Subscriptions stopped.',
          });
        }
      };
    },
    submit(input) {
      if (!transport)
        return Promise.reject(
          new Error('Live controller integration is unavailable.'),
        );
      const version = state.advisories
        .filter((a) => a.id === input.advisoryId)
        .sort((a, b) => b.version - a.version)[0];
      if (
        !version ||
        version.version !== input.version ||
        version.snapshotId !== input.snapshotId
      )
        return Promise.reject(
          new Error('This advisory changed. Review the latest version.'),
        );
      const blocked = decisionBlockReason(state, version, Date.now());
      if (blocked) return Promise.reject(new Error(blocked));
      if (input.kind === 'edit') {
        const keys = Object.keys(input.parameters);
        if (
          !input.rationale.trim() ||
          !version.parameters.length ||
          keys.length !== version.parameters.length ||
          version.parameters.some(
            (p) => !validParameter(p, input.parameters[p.key]),
          )
        )
          return Promise.reject(
            new Error(
              'Use the supplied parameter bounds and enter a rationale.',
            ),
          );
      }
      const normalized = structuredClone(input);
      if (normalized.kind === 'edit')
        normalized.parameters = Object.fromEntries(
          Object.entries(normalized.parameters).sort(([a], [b]) =>
            a.localeCompare(b),
          ),
        );
      const fingerprint = JSON.stringify([
        state.session?.identity,
        normalized.kind,
        normalized.advisoryId,
        normalized.version,
        normalized.snapshotId,
        normalized.kind === 'decision'
          ? normalized.decision
          : [normalized.parameters, normalized.rationale],
      ]);
      const previous = acknowledged.get(fingerprint);
      if (previous) return Promise.resolve(previous);
      const inflight = pending.get(fingerprint);
      if (inflight) return inflight;
      const conflicting = [...pending.keys()].some((key) => {
        const [, , advisoryId] = JSON.parse(key) as [string, string, string];
        return advisoryId === input.advisoryId;
      });
      if (conflicting)
        return Promise.reject(
          new Error('A request for this advisory is already pending.'),
        );
      const requestId = ids.get(fingerprint) ?? crypto.randomUUID();
      ids.set(fingerprint, requestId);
      const result = Promise.resolve()
        .then(() => transport.command({ ...normalized, requestId }))
        .then((ack) => {
          if (ack.requestId !== requestId)
            throw new Error(
              'Unexpected acknowledgement. Reload before retrying.',
            );
          acknowledged.set(fingerprint, ack);
          return ack;
        })
        .finally(() => {
          pending.delete(fingerprint);
        });
      pending.set(fingerprint, result);
      return result;
    },
  };
}
