// Public values only. Having configuration does not imply a live connection.
export const frontendConfig = Object.freeze({
  spacetimeWsUrl: import.meta.env.VITE_SPACETIMEDB_WS_URL?.trim() || '',
  spacetimeDatabase: import.meta.env.VITE_SPACETIMEDB_DATABASE?.trim() || '',
  displayTimezone:
    import.meta.env.VITE_DISPLAY_TIMEZONE?.trim() || 'America/Detroit',
});
