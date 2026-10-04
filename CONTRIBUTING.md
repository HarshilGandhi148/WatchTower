# Contributing with four people

Use the ownership table in docs/ownership.md. Create your branch from current
main in a separate clone or Git worktree. Never have multiple people switch
branches or edit the same working directory.

Branch examples: weather/metar-parser, congestion/demand-windows,
validation/decision-reducer, frontend/projection-drawer. Open a draft PR early
with the files and interfaces you expect to touch; link any dependency PRs.

## Shared change protocol

1. Describe a message/schema change in the PR before consumers depend on it.
2. Get review from the integration owner and affected producers/consumers.
3. Update contracts, producer, consumer, tests, and bindings together, or document
   an explicitly backwards-compatible rollout.
4. Rebase/update against current main, rerun make check, then merge.
5. Keep dependency updates separate; the integration owner resolves shared
   Python lockfiles, and the frontend owner resolves frontend lockfiles.

Changes to backend schema and frontend generated bindings belong in one PR.
Never manually resolve generated-code conflicts: regenerate from the merged schema.

## Review rules

One independent reviewer for ordinary code. Shared contracts, authorization,
live/projection writes, and decision state changes need both integration and
affected-owner review. Review fixtures as TEST input; do not allow them to
become runtime defaults.

Run make check and use the PR checklist. Include the failure cases tested,
limitations, and manual evidence where credentials are required. Skipped work
is still pending; it is not a passing acceptance test. No direct pushes to main
once branch protection is configured.

Optional local hooks: install pre-commit from requirements-dev.txt, then run
pre-commit install inside your own checkout. CI remains authoritative.
