# Codex Handoff

This handoff is for a fresh Codex conversation picking up after the auto-memory profiles release.

## Current Repo State

- Branch: `main`
- Remote state at handoff commit: aligned with `origin/main`
- Released commit: `7f4477e Implement auto-memory profiles (v1.13.0)`
- Released tag: `v1.13.0`
- Full regression at release: `PYTHONPATH=. pytest -q` -> `115 passed`
- Local dashboard smoke at release:
  - `/memories` returned 200
  - page contained `Auto Memory Profiles` and `Merge Now`
  - `/api/v2/memories/auto` returned one server profile with `pending_index: 1` and one `dm:bot:Nahida` profile
- Follow-up local working changes after the handoff commit:
  - Minor version staged locally as `v1.14.0`
  - Backlog item 1 implemented: legacy `/api/memories/*` routes no longer raw-mutate unified stores
  - Organic response splitting now catches missing punctuation before capitalized fresh thoughts such as `chest That's` and `dessert Self-destruct`
  - Latest local regression before release commit: `PYTHONPATH=. pytest -q` -> `123 passed`
  - Not committed/tagged/pushed unless the user explicitly requests it in the current conversation

## What Was Implemented

- Auto memory storage keeps one living `profile` entry per key:
  - Server: `server:{server_id}:user:{user_id}`
  - Per-bot DM: `dm:bot:{bot}:user:{user_id}`
- Provider merge failures keep or update one `pending` entry for that same key.
- Existing multi-entry auto-memory keys are loaded as `legacy` and queued for provider consolidation without destructive rewrite until merge succeeds.
- Memory context formatting includes the profile cleanly and includes pending facts only while awaiting merge.
- Dashboard/API now treat Auto Memories as Auto Memory Profiles:
  - One card per key
  - Shows profile/pending/legacy metadata
  - Shows pending merge state
  - Supports profile/pending edit and delete by key/index
  - Supports Merge Now by key and targeted user/scope merge
- README, CHANGELOG, and `version.py` were updated to `v1.13.0`.

## Important Files

- `memory.py`
  - Entry types: `profile`, `pending`, `legacy`
  - Main async merge/upsert path: `upsert_auto_memory_profile`
  - Consolidation path: `_merge_auto_memory_profile`, `llm_deduplicate`, `retry_pending_auto_profiles`
- `dashboard.py`
  - Auto profile API: `/api/v2/memories/auto`
  - Merge endpoint: `/api/v2/memories/auto/consolidate`
- `templates/memories.html`
  - Profile/pending rendering and Merge Now controls
- `bot_instance.py`
  - Uses per-bot DM memory scope for generated memory and scheduled reminder context
- `commands/memory.py`
  - Slash memory commands use per-bot DM memory scope
- Tests:
  - `tests/test_memory_manager.py`
  - `tests/test_dashboard_memory_api.py`

## Polish Backlog

Ranked backlog from the read-only crawl:

1. Done locally, not committed unless requested: retire or redirect legacy memory JSON mutation endpoints.
   Impact: High. Effort: Medium. Area: Memory/dashboard reliability.
   Old `/api/memories/*` endpoints have been routed through `MemoryManager` for unified stores; retired legacy file mutations return 410.

2. Unify dashboard JS rendering patterns.
   Impact: High. Effort: Medium. Area: Dashboard UX/maintainability.
   Several pages build large `innerHTML` strings with inline actions/styles. A shared rendering/helper layer would reduce XSS and layout drift risk.

3. Improve Reminders page ergonomics.
   Impact: Medium-high. Effort: Medium. Area: Reminders UX.
   Add search, due-date grouping, relative due badges, row actions, and clearer failed/skipped reason display.

4. Add dashboard tests for config/schedule/provider UI flows.
   Impact: Medium-high. Effort: Medium. Area: Test coverage.
   Backend schedule tests exist, but config saves, provider edits, restart/update actions, and UI rendering need more regression coverage.

5. Move direct config file writes behind service helpers.
   Impact: Medium. Effort: Medium. Area: Config reliability.
   `dashboard.py` still writes `providers.json`, `bots.json`, and related files directly in several routes.

6. Make memory merge jobs observable.
   Impact: Medium. Effort: Low-medium. Area: Memory UX.
   Expose last merge attempt, last error, and retry state in the dashboard so pending entries are explainable.

7. Split `dashboard.py` into route modules.
   Impact: Medium. Effort: High. Area: Cleanup.
   It currently owns config, memories, reminders, logs, updates, characters, and more.

8. Replace blocking browser confirms with app modals.
   Impact: Low-medium. Effort: Low. Area: UX polish.
   Several destructive dashboard actions still use `confirm()`.

## Suggested Next Step

If continuing immediately after the local `v1.14.0` changes, start with backlog item 2: unify dashboard JS rendering patterns.
