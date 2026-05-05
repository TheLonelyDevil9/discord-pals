# Quality Guardrails

These rules capture the project taste that should compound through future agent work. Add a mechanical check when a rule can be verified cheaply.

## Mechanical Checks

Run this before handing off code:

```bash
python tools/quality_check.py
python -m pytest
```

`tools/quality_check.py` verifies:

- `AGENTS.md` and `CLAUDE.md` stay in parity.
- The agent entrypoint links to the docs map instead of becoming a long manual.
- Core docs exist.
- Known oversized modules are tracked explicitly so growth is visible.
- Runtime config defaults and schema keys stay aligned.

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
