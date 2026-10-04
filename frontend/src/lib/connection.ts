import { DbConnection } from '../module_bindings';
import { frontendConfig } from './config';

export function createConnectionBuilder() {
  const { spacetimeWsUrl, spacetimeDatabase } = frontendConfig;
  if (!spacetimeWsUrl || !spacetimeDatabase) return undefined;
  try {
    const url = new URL(spacetimeWsUrl);
    if (!['ws:', 'wss:'].includes(url.protocol) || url.username || url.password)
      return undefined;
    if (
      url.protocol === 'ws:' &&
      !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
    )
      return undefined;
    return DbConnection.builder()
      .withUri(spacetimeWsUrl)
      .withDatabaseName(spacetimeDatabase);
  } catch {
    return undefined;
  }
}
