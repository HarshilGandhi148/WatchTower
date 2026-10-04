# GitHub enforcement setup

These settings require repository-admin access. They have not been applied by
creating this scaffold.

After pushing the scaffold and running CI once, protect main with a ruleset:

- Require pull requests and at least one independent approval.
- Require code-owner review after replacing the commented CODEOWNERS templates.
- Dismiss stale approvals and require approval of the most recent push.
- Require resolved review conversations and current/up-to-date CI.
- Require the check names: Python quality, JavaScript quality, Secret scan.
- Block force pushes, deletion, and direct pushes; apply the rule to admins too
  unless an explicit, documented emergency procedure is needed.
- Use squash merges to keep the four workstreams' changes easy to inspect.

For contracts, authorization, live/projection boundaries, and decisions, require
two reviewers through team policy: integration plus the affected owner.
CODEOWNERS lists several eligible owners but cannot require all of them by itself.

Replace each TODO owner with an actual @username with repo access, then uncomment
the corresponding CODEOWNERS line. Keep the general fallback first and specific
paths last; the last matching pattern wins.

Gitleaks scans history in CI with read-only permissions and comments/uploads
disabled. The action supports personal repositories without a license; if moving
to an organization, review its license requirements before that migration:
https://github.com/gitleaks/gitleaks-action.

CI never receives provider credentials. Credentialed smoke tests are run
separately by the relevant owner and summarized without exposing tokens.
