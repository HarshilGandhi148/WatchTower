// Infrastructure only. The official SDK handles subscriptions; Python handles decisions.
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { DbConnection } from '../frontend/src/module_bindings';

const allowed = [
  'ingest_event',
  'weather_reading',
  'congestion_reading',
  'validation_result',
] as const;
type Table = (typeof allowed)[number];
const uri = process.env.SPACETIMEDB_WS_URL ?? '';
const origin = process.env.SPACETIMEDB_HTTP_URL ?? '';
const database = process.env.SPACETIMEDB_DATABASE ?? '';
const token = process.env.SPACETIMEDB_BRIDGE_TOKEN ?? '';
const webhook =
  process.env.BRIDGE_WEBHOOK_URL ?? 'http://127.0.0.1:8090/events';
const secret = process.env.BRIDGE_WEBHOOK_SECRET ?? '';
const cursorPath =
  process.env.BRIDGE_CURSOR_PATH ?? '.cache/bridge-cursors.json';
const selected = (
  process.env.BRIDGE_SUBSCRIBE_TABLES ?? allowed.join(',')
).split(',');
function safeOrigin(value: string, protocols: string[]) {
  const url = new URL(value);
  if (
    !protocols.includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    (['http:', 'ws:'].includes(url.protocol) &&
      !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname))
  )
    throw new Error('Invalid bridge origin');
  return url;
}
const ws = safeOrigin(uri, ['ws:', 'wss:']);
const http = safeOrigin(origin, ['http:', 'https:']);
safeOrigin(webhook, ['http:', 'https:']);
if (
  ws.host !== http.host ||
  !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(database) ||
  !token ||
  secret.length < 32 ||
  selected.length === 0 ||
  new Set(selected).size !== selected.length ||
  selected.some((table) => !allowed.includes(table as Table))
)
  throw new Error('Invalid bridge configuration');
const tables = selected as Table[];
let cursors: Partial<Record<Table, string>> = {};
try {
  cursors = JSON.parse(readFileSync(cursorPath, 'utf8'));
} catch (error) {
  if ((error as NodeJS.ErrnoException).code !== 'ENOENT')
    throw new Error('Invalid cursor file');
}
for (const [table, value] of Object.entries(cursors)) {
  if (!allowed.includes(table as Table) || !/^[0-9]+$/.test(value ?? ''))
    throw new Error('Invalid cursor');
}
let connection: DbConnection | undefined;
let stopped = false;
let generation = 0;
let reconnectAttempt = 0;
let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
function persist() {
  mkdirSync(dirname(cursorPath), { recursive: true });
  writeFileSync(`${cursorPath}.tmp`, JSON.stringify(cursors), { mode: 0o600 });
  renameSync(`${cursorPath}.tmp`, cursorPath);
}
async function post(url: string, body: string, bearer: string, type: string) {
  const response = await fetch(url, {
    method: 'POST',
    body,
    redirect: 'error',
    headers: { Authorization: `Bearer ${bearer}`, 'Content-Type': type },
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response;
}
// SQL recovery includes every committed row beyond the acknowledged cursor. Sort locally:
// SpacetimeDB SQL need not support ORDER BY; never take LIMIT before sorting IDs.
async function recover(table: Table): Promise<{ id: bigint; at: string }[]> {
  const response = await post(
    `${origin.replace(/\/$/, '')}/v1/database/${database}/sql`,
    `SELECT * FROM ${table} WHERE id > ${cursors[table] ?? '0'}`,
    token,
    'text/plain',
  );
  const body = await response.text();
  if (body.length > 8 * 1024 * 1024)
    throw new Error('Backfill oversized; refusing incomplete delivery');
  const [result] = JSON.parse(body);
  const names = result.schema.elements.map(
    (column: { name: { some: string } }) => column.name.some,
  );
  return result.rows
    .map((row: unknown[]) => {
      const id = row[names.indexOf('id')];
      if (typeof id !== 'number' || !Number.isSafeInteger(id) || id <= 0)
        throw new Error('Invalid SQL sequence');
      const at = row[names.indexOf('created_at_utc')] as [number];
      return { id: BigInt(id), at: new Date(at[0] / 1000).toISOString() };
    })
    .sort((a: { id: bigint }, b: { id: bigint }) =>
      a.id < b.id ? -1 : a.id > b.id ? 1 : 0,
    );
}
// Inserts trigger a drain, rather than racing concurrent webhooks. The database is
// the durable queue. SQL also covers insert callbacks missed during disconnect.
let draining = false;
let requested = false;
async function drain() {
  requested = true;
  if (draining) return;
  draining = true;
  try {
    while (requested && !stopped) {
      requested = false;
      for (const table of tables) {
        const rows = await recover(table);
        for (const row of rows) {
          let attempt = 0;
          while (!stopped) {
            try {
              await post(
                webhook,
                JSON.stringify({
                  schema_version: 1,
                  table,
                  row_id: row.id.toString(),
                  sequence: row.id.toString(),
                  created_at_utc: row.at,
                }),
                secret,
                'application/json',
              );
              cursors[table] = row.id.toString();
              persist();
              break;
            } catch {
              console.error('Bridge delivery failed; retrying');
              await delay(Math.min(10000, 200 * 2 ** Math.min(attempt++, 6)));
            }
          }
          if (stopped) return;
        }
      }
    }
  } catch {
    console.error('Bridge backfill failed; retrying');
    setTimeout(() => {
      void drain();
    }, 1000);
  } finally {
    draining = false;
  }
}
function reconnect() {
  if (stopped || reconnectTimer) return;
  reconnectTimer = setTimeout(
    () => {
      reconnectTimer = undefined;
      connect();
    },
    Math.min(10000, 200 * 2 ** Math.min(reconnectAttempt++, 6)),
  );
}
function connect() {
  const current = ++generation;
  connection = DbConnection.builder()
    .withUri(uri)
    .withDatabaseName(database)
    .withToken(token)
    .onConnect((conn) => {
      reconnectAttempt = 0;
      const sources = {
        ingest_event: conn.db.ingestEvent,
        weather_reading: conn.db.weatherReading,
        congestion_reading: conn.db.congestionReading,
        validation_result: conn.db.validationResult,
      };
      for (const table of tables)
        sources[table].onInsert(() => {
          void drain();
        });
      conn
        .subscriptionBuilder()
        .onApplied(() => {
          console.log('Bridge subscribed');
          void drain();
        })
        .onError(() => {
          conn.disconnect();
          reconnect();
        })
        .subscribe(tables.map((table) => `SELECT * FROM ${table}`));
    })
    .onDisconnect(() => {
      if (current === generation) reconnect();
    })
    .onConnectError(() => {
      if (current === generation) reconnect();
    })
    .build();
}
function shutdown() {
  stopped = true;
  if (reconnectTimer) clearTimeout(reconnectTimer);
  connection?.disconnect();
}
process.on('SIGTERM', () => {
  shutdown();
  process.exit(0);
});
process.on('SIGINT', () => {
  shutdown();
  process.exit(0);
});
connect();
