## Change and workstream

Describe the resulting behavior and owner. Link any dependent/shared contract PRs.

## Validation

List actual checks run and relevant failed/missing-input cases.
Label credential-dependent checks that are still pending.

- [ ] Updated current main and ran make check.
- [ ] Kept cross-agent interfaces in shared contracts; affected owners reviewed.
- [ ] Added meaningful behavior tests where implementation changed.
- [ ] Kept fixtures and projections separate from live observations.
- [ ] Checked evidence, UTC/units, and unknown/stale behavior where applicable.
- [ ] Verified reducer authorization/version/race behavior for decision changes.
- [ ] Schema changes include regenerated bindings and recorded CLI version.
- [ ] No secrets in code, logs, bundles, fixtures, or attachments.
- [ ] Updated docs/quality-control.md only for gates actually verified.

## Remaining limitations

State incomplete integrations or product acceptance checks.
