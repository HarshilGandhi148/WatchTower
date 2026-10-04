// Runs only against the fresh TEST database supplied by the Python test runner.
import { DbConnection, tables } from '../../frontend/src/module_bindings';

import { reportFromJson } from '../../frontend/src/features/exchange/adapter';

const [uri, database] = process.argv.slice(2);
if (!uri?.startsWith('ws://127.0.0.1:') || !database?.startsWith('watchtower-test-')) throw new Error('TEST database required');
const emit = (event: object) => process.stdout.write(`TEST_EVENT ${JSON.stringify(event)}\n`);
const connections: DbConnection[] = [];
for (let client = 0; client < 2; client++) {
  const conn = DbConnection.builder().withUri(uri).withDatabaseName(database)
    .onConnect(connection => {
      connection.db.validationReports.onInsert((_ctx, row) => {
        const report = reportFromJson(JSON.parse(row.contentJson));
        emit({ client, event: 'report', checks: report.checks.length, snapshot: report.snapshot_id });
      });
      connection.db.weatherRecords.onInsert((_ctx, row) => emit({ client, event: 'weather', speed: row.windSpeedKt }));
      connection.db.weatherRecords.onUpdate((_ctx, _old, row) => emit({ client, event: 'weather', speed: row.windSpeedKt }));
      connection.db.aircraftObservations.onInsert((_ctx, row) => emit({ client, event: 'aircraft', id: row.aircraftId }));
      connection.subscriptionBuilder().onApplied(() => emit({ client, event: 'ready' })).onError(() => { emit({ event: 'error' }); process.exitCode = 1; }).subscribe([tables.weatherRecords, tables.aircraftObservations, tables.sourceHealth, tables.validationReports]);
    }).onConnectError(() => { emit({ event: 'error' }); process.exitCode = 1; }).build();
  connections.push(conn);
}
process.stdin.resume();
process.stdin.on('end', () => {
  for (const conn of connections) conn.disconnect();
  process.exit(0);
});
