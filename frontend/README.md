# Frontend workstream

Owner: TODO_FRONTEND_OWNER. Vite + React + TypeScript workspace, with strict
TypeScript, ESLint, Prettier, Vitest and Testing Library.

Run npm ci, copy .env.example to .env, then npm run dev.
npm run check runs all workspace checks and builds production assets.

The initial page is disconnected: there are no live values, projections or
decision controls. Implement dashboard layout, advisory queue, preview,
bounded edits and versioned decisions from plan 12. Keep styles accessible,
keyboard-operable and explicit about observed versus projected values.

src/lib/config.ts is the only browser env-access boundary. Keep credentials
in backend services/per-user sessions. The three empty public DB settings are
reserved for future SpacetimeDB integration. Add the official client SDK at the
same exact tested version as backend/module, then generate module_bindings/
from that module. Never hand-copy database rows or hand-edit generated bindings.

Frontend component tests stay with features/components; browser integration
rehearsals live in tests/e2e/. A disconnected placeholder test is not evidence
that the planned controller flow works.
