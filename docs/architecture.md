# Architecture

Discord Pals is a single-process Discord bot with a local Flask dashboard. The project is intentionally small-file-count Python, so the most useful architectural rule is clear ownership rather than deep package layering.

## Runtime Domains

| Domain | Primary Files | Owns | Should Not Own |
| --- | --- | --- | --- |
| Startup and release | `main.py`, `startup.py`, `bump_version.py`, `version.py` | process boot, validation, version bumping | dashboard request handling |
| Discord orchestration | `bot_instance.py`, `coordinator.py`, `commands/` | Discord events, slash commands, request concurrency | raw provider config parsing |
| Provider calls | `providers.py`, `request_queue.py` | OpenAI-compatible requests, fallback order, provider runtime behavior | memory persistence |
| Memory and reminders | `memory.py`, `reminders.py`, `time_utils.py` | unified stores, reminder scheduling, timezone resolution | dashboard template structure |
| Dashboard | `dashboard.py`, `templates/`, `images/`, `security.py` | local UI, dashboard APIs, auth and CSRF | Discord event decisions |
| Shared utilities | `discord_utils.py`, `logger.py`, `response_sanitizer.py`, `scopes.py`, `constants.py` | history helpers, logging, output cleanup, identifier parsing | feature-specific business logic |

## Boundary Rules

- Parse external data at the boundary. JSON from files, dashboard POST bodies, and provider responses should be coerced into known shapes before hot-path code reads it.
- Dashboard-first still applies: runtime settings and durable data structures should be visible or editable through the dashboard when that is sensible.
- Prefer a named helper for a repeated invariant. Do not duplicate path checks, JSON fallback handling, scope parsing, or provider fallback logic inline.
- Keep user-authored character and prompt text as text. Normalize line endings when writing from browser forms, but avoid semantic rewriting.
- Use `discord_utils.safe_json_load` and `safe_json_save` for bot data files unless a route needs to validate raw editor text before saving.

## Refactor Direction

The large modules should shrink along ownership lines:

- Extract dashboard request parsing and response serializers when a route family grows.
- Extract Discord response lifecycle helpers from `bot_instance.py` only when tests can cover the behavior before and after the move.
- Keep memory migrations near `memory.py` until there is a dedicated migrations module with tests.

Small, tested extractions are better than sweeping movement because bot behavior depends on Discord objects that are hard to reproduce manually.
