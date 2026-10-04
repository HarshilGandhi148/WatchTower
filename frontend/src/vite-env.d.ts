/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SPACETIMEDB_WS_URL?: string;
  readonly VITE_SPACETIMEDB_DATABASE?: string;
  readonly VITE_DISPLAY_TIMEZONE?: string;
}
