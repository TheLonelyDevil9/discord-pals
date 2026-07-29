# Quality Guardrails

These rules capture the project taste that should compound through future agent work. Add a mechanical check when a rule can be verified cheaply.

## Mechanical Checks

Run this before handing off code:

```bash
python tools/quality_check.py
python -m pytest
python tools/dashboard_smoke.py
```

`tools/quality_check.py` verifies:

- `AGENTS.md` and `CLAUDE.md` stay in parity.
- The agent entrypoint links to the docs map instead of becoming a long manual.
- Core docs exist.
- Known oversized modules are tracked explicitly so growth is visible.
- Runtime config defaults and schema keys stay aligned.

`tools/dashboard_smoke.py` boots the real Waitress server with `DASHBOARD_PASS` set and fails if the liveness probe is unreachable or if any dashboard page or API answers without a login. The unit tests use the Flask test client, which never binds a socket.

## Continuous Integration

`.github/workflows/ci.yml` runs on pushes and pull requests targeting `main` or `staging`:

| Job | What it protects |
| --- | --- |
| Quality checks | `tools/quality_check.py` guardrails |
| Lint | Ruff correctness rules only: undefined and redefined names, syntax errors, identity comparison against literals |
| Tests | Full suite on Python 3.10, 3.12, and 3.13 |
| Dashboard smoke test | The authenticated access boundary against a running server |
| Security scan | Bandit at high severity and high confidence; `pip-audit` advisory-only |

Lint deliberately runs a narrow rule set. The codebase has hundreds of pre-existing style findings, and a job that always fails is a job everyone learns to ignore. `pip-audit` does not fail the build because the pinned Flask, Waitress, and python-dotenv versions have open advisories; bumping those pins is separate work.

## Invariants

- Runtime config keys in `runtime_config.DEFAULTS` must have matching `CONFIG_FIELDS` entries unless they are deliberately read-only runtime state.
- Dashboard APIs must reject malformed JSON objects before iterating over payloads.
- New runtime settings belong in `runtime_config.py`, must be dashboard-visible, and need tests for invalid values when they influence bot behavior.
- Legacy memory JSON endpoints stay retired; use v2 memory APIs and the unified stores.
- Provider fallback, output sanitization, and impersonation prevention should stay centralized.
- Authenticated dashboard write routes must keep CSRF protection.

## Review Focus

For code review, lead with behavior and risk:

- Security: dashboard auth, path handling, CSRF, token leakage, unsafe imports.
- Correctness: Discord event ordering, multi-bot isolation, memory scope isolation, provider fallback behavior.
- Maintainability: duplicated boundary parsing, growing large modules, undocumented new data files.
- Tests: regression coverage for route payloads, memory shape changes, and runtime settings.
