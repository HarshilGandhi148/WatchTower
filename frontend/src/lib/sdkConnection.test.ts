import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createConnectionBuilder } from './connection';

const config = vi.hoisted(() => ({
  spacetimeWsUrl: '',
  spacetimeDatabase: '',
}));
vi.mock('./config', () => ({ frontendConfig: config }));
beforeEach(() => {
  config.spacetimeWsUrl = '';
  config.spacetimeDatabase = '';
});

describe('public connection configuration', () => {
  it('does not create a connection from missing settings', () => {
    expect(createConnectionBuilder()).toBeUndefined();
    config.spacetimeWsUrl = 'ws://127.0.0.1:3000';
    expect(createConnectionBuilder()).toBeUndefined();
  });
  it.each([
    'not-a-url',
    'https://localhost',
    'ws://remote.example',
    'wss://user:secret@example.org',
  ])('rejects invalid/insecure origin %s', (uri) => {
    config.spacetimeDatabase = 'watchtower-local';
    config.spacetimeWsUrl = uri;
    expect(createConnectionBuilder()).toBeUndefined();
  });
  it.each(['ws://127.0.0.1:3000', 'wss://example.org'])(
    'accepts configured SDK connection %s',
    (uri) => {
      config.spacetimeDatabase = 'watchtower-local';
      config.spacetimeWsUrl = uri;
      expect(createConnectionBuilder()).toBeDefined();
    },
  );
});
