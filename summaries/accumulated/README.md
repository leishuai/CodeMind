# Accumulated Summaries

Machine-global lessons accumulated automatically by CodeMind task summaries.
This area lives in the CodeMind runtime install and is **install-exclude
protected** (see `install.sh --exclude='summaries/accumulated/'`), so entries
written here survive runtime re-installs/updates.

Two destinations only:

- `technical/` — cross-project, business-agnostic, public-safe lessons. No
  private project names, bundle identifiers, device IDs, signing teams, local
  absolute paths, or raw task logs.
- `business/<slug>/` — project-bound lessons reusable across that project's
  tasks. Allowed to reference concrete packages/paths/domains. Never committed
  to the public product package.

Routing is automatic: each accumulated lesson goes to `business/<slug>/` when it
looks project-specific (bundle id, absolute path, device UDID, or project slug
token), otherwise to `technical/`. Entries are de-duplicated by a canonical key
and each file is capped (`AUTOMIND_ACCUMULATED_MAX_ENTRIES`, default 200).

Promotion of validated lessons into the maintainer-distributed
`summaries/preloaded/` packs is a maintainer-only, git-based step and is
intentionally not automated.
