import { describe, expect, it, vi } from 'vitest';
import { Identity, Timestamp } from 'spacetimedb';
vi.mock('spacetimedb/server', () => ({
  t: { string: () => ({}), u64: () => ({}) },
}));
vi.mock('../src/schema', () => ({
  database: { reducer: (_: unknown, body: unknown) => body },
}));
import {
  write_ingest_event,
  write_weather_reading,
  write_validation_result,
} from '../src/reducers/streams';
const identity = Identity.fromString('1'.repeat(64));
const outsider = Identity.fromString('2'.repeat(64));
type Row = {
  id: bigint;
  eventKey: string;
  rootId: bigint;
  createdAtUtc: Timestamp;
  payloadJson: string;
};
function fixture() {
  const rows = new Map<string, Row>();
  const table = {
    eventKey: { find: (key: string) => rows.get(key) },
    id: {
      find: (id: bigint) => [...rows.values()].find((row) => row.id === id),
    },
    iter: () => rows.values(),
    insert: (row: Row) => {
      rows.set(row.eventKey, { ...row, id: BigInt(rows.size + 1) });
    },
  };
  const ctx = {
    sender: identity,
    timestamp: new Timestamp(1n),
    db: {
      agentIdentity: { name: { find: () => ({ identity }) } },
      ingestEvent: table,
      weatherReading: table,
      congestionReading: table,
      validationResult: table,
    },
  };
  // Pure body mock does not implement the SDK reducer object; real server tests cover that.
  const run = (
    reducer: unknown,
    key: string,
    rootId: bigint,
    payloadJson = '{"schema_version":1}',
  ) =>
    (reducer as (ctx: unknown, input: unknown) => void)(ctx, {
      eventKey: key,
      rootId,
      payloadJson,
    });
  return { rows, ctx, run };
}
describe('durable stream reducers', () => {
  it('rejects another sender, missing roots and invalid derived keys', () => {
    const { ctx, run } = fixture();
    ctx.sender = outsider;
    expect(() => run(write_ingest_event, 'input', 0n)).toThrow('Unauthorized');
    ctx.sender = identity;
    expect(() => run(write_weather_reading, 'weather:9', 9n)).toThrow(
      'Missing ingestion',
    );
    expect(() => run(write_weather_reading, 'other-key', 1n)).toThrow(
      'Invalid specialist key',
    );
    expect(() => run(write_ingest_event, 'input', 1n)).toThrow(
      'Ingestion cannot',
    );
  });
  it('canonical duplicate is a no-op, conflicting retry fails', () => {
    const { rows, run } = fixture();
    run(write_ingest_event, 'input', 0n, '{"schema_version":1,"value":2}');
    run(write_ingest_event, 'input', 0n, '{"value":2,"schema_version":1}');
    expect(rows.size).toBe(1);
    expect(() =>
      run(write_ingest_event, 'input', 0n, '{"schema_version":1,"value":3}'),
    ).toThrow('Conflicting');
  });
  it('requires both specialists before a verdict', () => {
    const { run, ctx } = fixture();
    run(write_ingest_event, 'input', 0n);
    ctx.db.weatherReading = {
      ...ctx.db.weatherReading,
      iter: () => new Map<string, Row>().values(),
    };
    expect(() => run(write_validation_result, 'validation:1', 1n)).toThrow(
      'Missing specialist',
    );
  });
  it('rejects malformed payloads and oversized writes', () => {
    const { run } = fixture();
    for (const payload of '[],{}, {"schema_version":2}'.split(',')) {
      expect(() => run(write_ingest_event, 'input', 0n, payload)).toThrow();
    }
    expect(() =>
      run(write_ingest_event, 'input', 0n, 'x'.repeat(1024 * 1024 + 1)),
    ).toThrow('Payload too large');
  });
});
