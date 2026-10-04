# Frontend workstream

Owner: TODO_FRONTEND_OWNER. Vite + React + TypeScript workspace, with strict
TypeScript, ESLint, Prettier, Vitest and Testing Library.

Run npm ci, copy .env.example to .env, then npm run dev.
npm run check runs all workspace checks and builds production assets.

The page subscribes to the three public observation tables when configured,
showing connection readiness, provider health, METAR and nearby aircraft. Empty
data remains unknown; disconnected caches are stale. The official SDK reconnects
with backoff and subscriptions must reload before data is current. There are no
projections or decision controls. Implement dashboard layout, advisory queue, preview,
bounded edits and versioned decisions from plan 12. Keep styles accessible,
keyboard-operable and explicit about observed versus projected values.

src/lib/config.ts is the only browser env-access boundary. Keep credentials
in backend services/per-user sessions. The example public settings target the local database.
The official client SDK is pinned to 2.10.2, matching backend/module. Bindings
in module_bindings/ are generated from that module. Never hand-copy database rows or hand-edit generated bindings.

Frontend component tests stay with features/components; browser integration
rehearsals live in tests/e2e/. A disconnected placeholder test is not evidence
that the planned controller flow works.
