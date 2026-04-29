# Changelog

All notable changes to Discord Pals will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [v2.0.3] - 2026-04-29

### Fixed

- Dashboard character and system prompt saves now normalize browser textarea line endings before writing files, preventing extra blank lines from accumulating on Windows.
- Regression coverage now verifies character and system prompt saves preserve intended blank lines without adding new ones.

### Changed

- Dashboard updates now use a fetch-based flow with single-update locking, stale Git lock recovery, dirty-worktree stashing/restoration, backup branches for diverged checkouts, and dependency-install fallbacks with warnings.

## [v2.0.2] - 2026-04-28

### Fixed

- Multi-bot setup script re-runs on Windows and macOS/Linux now keep `bots.json` refreshed and can append newly configured bot token variables to an existing `.env`.
- Startup validation now checks `bots.json` structure and reports missing multi-bot token environment variables before launch.
- Multi-bot startup no longer requires the single-bot `DISCORD_TOKEN` when `bots.json` is present.

### Changed

- README setup, multi-bot, and troubleshooting docs now describe setup re-runs and multi-bot token validation.
- Agent instructions now allow the documented release flow to commit, tag, and push after changes.

## [v2.0.1] - 2026-04-28

### Fixed

- DM follow-ups now use the same organic response splitting as normal replies, so model line breaks become separate Discord messages when intended.
- Response splitting now repairs missing terminal punctuation at inferred split boundaries to preserve character cadence.
- Follow-up prompts now explicitly preserve normal punctuation instead of using line breaks as punctuation.

### Changed

- Refresh project banner image
- Update project banner image

## [v2.0.0] - 2026-04-27

### Added

- **Major checkpoint release** - marks the dashboard, memory-profile, scheduling, and documentation cleanup work as the v2 baseline

### Changed

- **Documentation alignment** - README, contributing notes, examples, and inline security/runtime comments now match the current dashboard, command, memory, provider, and scheduling behavior
- **Local handoff cleanup** - removed the tracked Codex handoff artifact and ignored future `CODEX_HANDOFF.md` files
- **Runtime comments** - clarified the legacy `custom_nicknames` runtime field in favor of per-bot dashboard-managed nicknames

### Fixed

- **Availability schedules** - DM follow-ups now respect per-bot unavailable schedule windows, matching reminder and autonomous-response behavior
- Regression coverage for DM follow-ups being skipped during unavailable schedule windows

## [v1.14.0] - 2026-04-27

### Changed

- **Legacy memory endpoints** - old `/api/memories/*` compatibility routes now delegate unified auto-memory and manual-lore mutations through `MemoryManager`, while retired legacy file mutations return 410
- **Raw memory editor** - unified memory JSON files are now read-only in the raw editor so dashboard users do not bypass profile/pending invariants

### Fixed

- **Auto-memory profile integrity** - legacy deduplication no longer rewrites auto-memory profile lists directly; auto profile cleanup is routed through the merge endpoint instead
- **Organic response splitting** - long single-paragraph replies now split when the model omits punctuation before a capitalized fresh thought, including cases like `chest That's` and `dessert Self-destruct`
- Regression coverage for read-only raw unified memory stores, manager-backed legacy entry edits/deletes, legacy bulk delete routing, retired legacy endpoint responses, safe deduplication behavior, and missing-punctuation response splitting

## [v1.13.0] - 2026-04-26

### Added

- **Auto-memory profiles** - auto memories now use one living `profile` entry per server/user or per-bot DM/user key, with a single `pending` entry only when provider merging fails
- **Immediate profile merging** - newly learned facts merge into the existing profile through the provider path instead of accumulating long lists
- **Manual Merge Now** - the dashboard/API can merge specific auto-memory keys as well as targeted users/scopes
- Regression coverage for profile creation, per-bot DM scope labels, immediate merge success/failure, pending retry, legacy migration, targeted merge, and pending edit/delete

### Changed

- **Memories dashboard** - Auto Memories are reframed as Auto Memory Profiles, rendered one card per key with profile/pending metadata and merge status
- **Memory context formatting** - model context now includes the consolidated profile cleanly and only includes pending facts while a merge is awaiting retry
- **Docs** - README now documents profile/pending entry types and the per-bot DM memory key format

### Fixed

- **DM memory scope consistency** - slash commands, scheduled reminder context, mentioned-user DM checks, and dashboard labels now preserve per-bot DM memory namespaces
- **Legacy auto-memory cleanup** - existing multi-entry keys are queued for background consolidation without destructive rewrites until an LLM merge succeeds

## [v1.12.0] - 2026-04-26

### Added

- **Flexible bot schedules** - dashboard availability controls now support multiple unavailable windows per bot
- Regression coverage for multi-window schedule saves and overnight availability blocking

### Changed

- **Config dashboard organization** - the Config page is now grouped into Runtime, Characters, Scheduling, Conversation, Providers, Maintenance, and Advanced sections with a sticky section navigator
- **Schedule editing** - per-bot schedule rows are now structured panels with timezone selection and add/remove window actions
- **Docs** - README now documents bot availability schedules and the `bot_schedules` runtime setting

### Fixed

- **Overnight availability windows** - windows such as Friday 22:00 to 08:00 now correctly block the next morning

## [v1.11.5] - 2026-04-24

### Added

- **Scope helpers** — centralized local history, DM memory, stats, and display-label identifiers in `scopes.py` with lightweight typed request, memory, and delivery context dataclasses
- Regression coverage for bot/user DM history isolation, bot/user DM memory isolation, typed request scopes, and release tag ordering

### Changed

- **Release flow** — `bump_version.py --tag` now commits version/changelog updates before creating the git tag so future release tags point at release commits
- **Runtime scope construction** — bot runtime, memory, and Discord history helpers now use the shared scope helpers instead of scattered ad-hoc key construction

### Fixed

- **Tag hygiene** — corrected local `v1.11.4` to point at release commit `ae24002`

## [v1.11.4] - 2026-04-24

### Added

- **Per-character availability schedules** - dashboard controls now let each bot define unavailable time windows, with autonomous responses and DM follow-ups paused during those periods
- **Server-to-DM shortcut** - bots can now open a DM when a user asks them in server context to message them privately
- **Emoji spam guard** - bot responses now enforce a rolling emoji budget of one emoji per response and at most two emojis across five responses
- Regression coverage for DM follow-up history isolation, schedule blocking, DM invite detection, and emoji budget enforcement

### Changed

- **DM follow-up prompts** - autonomous DM follow-ups now ask for a fresh topic or angle and require fuller 2-3 sentence messages with something concrete to answer
- **History clearing command** - conversation history clearing moved from `/clear` to grouped `/history clear` so it is easier to distinguish from `/timezone clear` in large multi-bot installs

### Fixed

- **DM memory isolation** - DM memories are now scoped by bot and user instead of sharing one DM memory bucket across all characters
- **DM context isolation** - local DM conversation history now uses bot/user-specific keys to prevent separate users' DMs from contaminating each other's context
- **Timezone contamination** - DM prompt context now resolves user timezones from the isolated target-user context instead of shared/stale DM history context
- **Dashboard text saves** - character and prompt textareas no longer inject extra leading/trailing whitespace on each save

## [v1.11.3] - 2026-04-02

### Added

- Dashboard-visible slash-command sync status, plus a read-only `/api/command-sync-status` endpoint for remote verification
- Regression coverage for grouped slash-command registration/sync, command status rendering, explicit character schema parsing, preview gating, and sentence-based reply bursting

### Changed

- **Slash-command rollout** — bots now sync commands globally and then copy/sync them per connected guild at startup so grouped commands like `/timezone set` and `/reminders list` appear faster to end users
- **Command audit** — grouped `/timezone` and `/reminders` commands stay in place, while maintenance/admin commands are now classified separately and restricted with Discord-side default permissions where supported
- **Character markdown schema** — new character files now scaffold with `## System Persona`, `## Example Dialogue`, and `## User Context`, and the preview UI now shows always-injected, gated, and ignored sections explicitly
- **Character prompt injection** — character-file headings are now parser-only; runtime prompt assembly injects tagged blocks like `<character_persona>`, `<example_dialogue>`, and `<special_context>` instead of reinserting markdown headers
- **Docs** — command troubleshooting now points to restart, sync status, and `applications.commands` scope guidance, and character docs no longer claim provider preference lives in the markdown file

### Fixed

- **Grouped command discoverability** — startup logs now print grouped slash-command inventory clearly, reducing confusion around `/timezone` and `/reminders` not appearing as flat commands
- **Character preview routing** — the legacy `/preview` route now redirects to the merged Characters page instead of targeting a missing template
- **Legacy user-context parsing** — legacy `## Special Users` files now preserve fuzzy user matching correctly under the new parser
- **Natural multi-message fallback** — long one-line replies without explicit newlines can now split into multiple Discord sends on sentence boundaries, so chat pacing still works when the model returns one flat paragraph
## [v1.11.2] - 2026-04-01

### Added

- Regression coverage for guild-wide mention resolution, ambiguous-name safety, natural multi-message delivery, and per-message assistant history persistence

### Changed

- **Mention conversion** — outgoing `@Name` resolution now prioritizes explicit current-message mentions and reply targets first, recent human participants second, and visible guild members last
- **Natural reply pacing** — generated line breaks now survive to Discord more naturally: double newlines always split, short self-contained single-newline thoughts may split, and one response burst is capped at 3 Discord messages
- **Assistant history shape** — multipart bot replies are now stored as separate assistant turns with per-message IDs and timestamps instead of one collapsed assistant blob

### Fixed

- **Guild member tagging coverage** — bots can now convert `@Name` into a real Discord mention even when that member has not spoken recently in the channel
- **Ambiguous mention safety** — duplicate same-priority display-name matches now stay plain text instead of risking a ping to the wrong user
- **Deleted bot-message cleanup** — assistant history removal now targets the exact deleted Discord message by ID before falling back to tail-pruning

## [v1.11.1] - 2026-04-01

### Added

- Dashboard bot-timezone picker backed by the app's valid IANA timezone list, plus `/timezone set` autocomplete suggestions sourced from the same list
- Regression coverage for system-prompt `{{CURRENT_TIME_CONTEXT}}` propagation, explicit mention resolution, friendly auto-memory labels, dashboard timezone picker rendering, and manual-lore editing

### Changed

- **Mention visibility** — current-message and reply/reference mentions now prefer live Discord mention objects over guild-cache lookups, so tagged users resolve into readable names more reliably
- **Mention context propagation** — explicitly tagged users are now surfaced into prompt context and mentionable-user context, making it clearer to the model that they are real people in the current conversation
- **Memory dashboard labels** — auto-memory cards now favor human-readable guild names and friendly scope labels instead of exposing raw storage keys in the main UI

### Fixed

- **`{{CURRENT_TIME_CONTEXT}}` in system prompts** — the placeholder now resolves in the system-prompt render path as well as chatroom context, without changing the prompt text itself
- **Manual lore editing** — the Memories dashboard now exposes in-place lore editing instead of requiring raw JSON edits
- **Oversized dashboard checkboxes** — checkbox inputs no longer inherit full text-input sizing, preventing giant selection boxes on the Memories page

## [v1.11.0] - 2026-04-01

### Added

- Durable one-shot in-character reminders with persistent storage in `bot_data/reminders.json`
- Per-user timezone storage in `bot_data/user_timezones.json`, with `/timezone set`, `/timezone show`, and `/timezone clear`
- `/reminders list` and `/reminders cancel` slash commands for reminder inspection and cancellation
- Dashboard reminder queue with filtering and bulk cancellation
- Bot-level timezone fallbacks in the dashboard Config page
- Regression coverage for reminder deduplication, reminder scheduling/delivery state, reminder dashboard APIs, timezone precedence, and provider token-limit behavior

### Changed

- **Reminder scheduling** — reminder text is now generated at send time using fresh character context/history instead of storing a prewritten line at creation
- **Prompt time resolution** — prompt time-awareness now shares the same timezone precedence used by reminders: user timezone, then bot timezone, then process timezone
- **Timezone support on Windows and similar environments** — added `tzdata` dependency so IANA timezone names work reliably cross-platform

### Fixed

- **Claude/Opus token handling** — removed the local `450` max-token cap so provider-configured or request-configured token limits are respected directly
- **Reminder ownership** — reminders now belong to the specific bot that handled the interaction, so multiple bots do not all schedule the same future ping
- **Reminder delivery fallback** — reminders now fall back to DM if the original server channel is unavailable at send time

## [v1.10.11] - 2026-04-01

### Added

- Broad prompt time placeholders, including `{{time}}`, `{{date}}`, `{{weekday}}`, `{{day}}`, `{{month}}`, `{{month_name}}`, `{{year}}`, `{{hour}}`, `{{minute}}`, `{{second}}`, `{{datetime}}`, `{{iso_datetime}}`, `{{timezone}}`, `{{utc_offset}}`, and `{{unix}}`
- Regression coverage for prompt time placeholder rendering, persisted history timestamps, and long-gap history formatting

### Changed

- **Chatroom time awareness** — chatroom context now injects the current local date/time automatically, so bots can react to mornings, weekdays, day-of-month, and similar time-sensitive context without editing the system prompt text
- **History timestamps** — stored conversation history entries now persist message timestamps so future prompt assembly can reason about when messages actually happened

### Fixed

- **Long-gap awareness** — when a reply arrives after a significant pause, the active conversation context now includes a readable gap marker such as `Time gap: 1 day later`, so the bot can tell the response was not immediate
- **Prompt placeholder propagation** — time placeholders are now expanded across rendered prompt sections, including character persona text, special-user context, and chatroom context
- **Chatroom character placeholder preview** — chatroom context rendering now receives the active character name consistently, so `{{CHARACTER_NAME}}` resolves correctly in preview/runtime paths

## [v1.10.10] - 2026-03-23

### Added

- Regression coverage for split-reply target processing, in-flight queue duplicate suppression, safe reply-reference caching, and failed-send history handling

### Fixed

- **Split reply request dropping** — one Discord message can now be processed once per target user, so multi-user split replies no longer discard the second and later targets as duplicate work
- **Queue in-flight suppression** — pending request counts and duplicate signatures now stay active until processing actually finishes, preventing same-user duplicates from slipping back into the queue while the first request is still running
- **Phantom assistant history** — assistant turns are now written to history only after Discord successfully delivers content, so failed sends stop poisoning future context with replies that never appeared in chat
- **Reply reference caching safety** — referenced-message resolution now uses an internal cache instead of mutating Discord message objects, avoiding production failures on slot-based message implementations

## [v1.10.9] - 2026-03-23

### Added

- Regression coverage for reply-thread context propagation, ensuring referenced-author context is preserved for short reply messages

### Fixed

- **Reply targeting drift** — human replies to another user or bot now carry explicit reply-thread context into the prompt, so the model keeps the current sender as the conversational target instead of drifting back to the previous speaker

## [v1.10.8] - 2026-03-23

### Added

- Regression coverage for attachment-only visual context, including emoji-only plain-text payloads and retained image attachment handling

### Fixed

- **Emoji vision rollback** — custom and Unicode emojis are no longer converted into image references inside model prompts, reducing the prompt expansion and repetition risk introduced by emoji visual enrichment
- **Attachment-only multimodal scope** — real image uploads continue to use the multimodal request path, while emoji text and shortcodes remain plain text context
- **Text-only fallback compatibility** — non-vision and misdeclared-vision providers still receive the current text-only fallback behavior for real image attachments after the emoji-vision rollback

### Removed

- Emoji-as-image prompt enrichment and its related helper/dependency surface

## [v1.10.7] - 2026-03-23

### Added

- Regression coverage for runtime image-input rejection fallback, ensuring providers that unexpectedly reject multimodal input are retried as text-only on the same tier

### Fixed

- **Runtime vision fallback** — providers that return errors like `No endpoints found that support image input` now automatically retry with the text-only version of the same request instead of failing the turn
- **Capability caching** — once a provider rejects image input at runtime, that tier is temporarily treated as text-only for the rest of the current process so future requests stop sending it multimodal payloads

## [v1.10.6] - 2026-03-23

### Added

- Regression coverage for mixed vision/text-only provider handling, including single-user formatting preservation for non-vision models when emoji or attachment visuals are present

### Fixed

- **Text-only provider compatibility** — requests with emoji/image visual enrichment now strip back to the normal single-user text format for non-vision models instead of leaving them in the multimessage fallback shape
- **Provider parity** — vision-capable models continue receiving multimodal requests while text-only models now reliably receive the equivalent text-only request for the same prompt/context

## [v1.10.5] - 2026-03-23

### Added

- Regression coverage for public provider-failure notices, ensuring channel-visible fallback errors use Discord auto-delete

### Fixed

- **Public provider failure notices** — the direct `Something went wrong - all providers failed.` channel message now auto-deletes after the normal transient-error timeout instead of remaining visible in public chat

## [v1.10.4] - 2026-03-23

### Added

- Regression coverage for `/interact` target pinning and cross-thread context isolation

### Changed

- **`/interact` request routing** — synthetic interaction requests now persist an explicit invoking-user target through the queue so context building stays anchored to the slash-command user instead of inheriting channel reply targeting
- **`/interact` history assembly** — slash-command interactions now use only the invoking user's own recent turns plus directly-following replies from the current bot, preventing unrelated channel conversations from bleeding into the generated response

### Fixed

- **`/interact` cross-thread replies** — using `/interact` while the bot is queued on another user or bot in the same channel no longer sends you a response that was generated for that other conversation

## [v1.10.3] - 2026-03-23

### Added

- Regression coverage for active memory target lists and emoji vision enrichment, including custom emoji, Unicode emoji, dedupe caps, and text-only provider fallback behavior
- A new `GET /api/v2/memories/targets` endpoint for live auto-memory and user-lore target pickers

### Changed

- **Memories target pickers** — the Auto Memories and Manual Lore bulk-action user lists now load from the live unified memory stores instead of stale stats caches, so deleted users disappear as soon as their last matching memory is removed
- **Vision context assembly** — vision-capable requests now enrich the active conversation window with labeled emoji image references while keeping text-only providers on the existing fallback path

### Fixed

- **Stale target users** — users with no active auto memories or user lore no longer appear in the dashboard target-user pickers
- **Emoji visual grounding** — custom Discord emojis and Unicode emojis can now be sent as inline visual references for vision-capable providers, including recent user and assistant turns already present in context
- **Text-only multimodal fallback wording** — stripped image notes now use a generic visual-reference message instead of implying every omitted image came from a user attachment

## [v1.10.2] - 2026-03-23

### Added

- Regression coverage for DM follow-up delivery when the private channel is no longer available from the in-memory Discord channel cache

### Changed

- **DM follow-up processing** — the background loop now runs through a shared single-pass helper, making the delivery logic easier to test and more reliable across cache misses

### Fixed

- **DM follow-up delivery** — follow-ups now recover private channels via `fetch_channel`, cached/fetched users, and `create_dm()` instead of silently skipping when `client.get_channel()` cannot resolve an older DM
- **DM follow-up observability** — the bot now logs when a due follow-up cannot resolve its DM channel or when generation returns an empty/error response instead of failing quietly

## [v1.10.1] - 2026-03-22

### Added

- Regression coverage for single-user prompt propagation, assistant-author preservation, and runtime config key migration

### Changed

- **Single-user prompt flattening** — injected chatroom context is now labeled as context instead of a second instructions block, preserving the system prompt hierarchy without changing prompt text
- **Runtime config compatibility** — legacy `context_message_count` reads and writes are now normalized to `user_only_context_count`, and the dashboard config UI now uses the current key consistently

### Fixed

- **Character voice anchoring** — current-bot assistant turns now retain their author name in both user-only and legacy history formatting, so flattened prompts keep prior replies attributed to the active character instead of generic `Assistant`
- **Synthetic first-turn ordering** — example-dialogue fallback turns now keep the character author and stay after system/context entries, preventing the first-turn anchor from weakening prompt precedence

## [v1.10.0] - 2026-03-21

### Added

- Delta dashboard endpoints for status, logs, and context polling, plus regression coverage for queue behavior, version caching, topology deduplication, and per-channel history persistence

### Changed

- **Dashboard refresh path** — the Dashboard and Logs pages now use incremental polling with keyed DOM patching instead of full container rewrites, reducing flicker and unnecessary work during live updates
- **Discord topology lookups** — a shared 10-second visible-topology cache now backs the dashboard, memories, config, and channel-management views instead of repeatedly walking every bot, guild, and channel on each request
- **Request queue internals** — per-channel queues now use `deque` FIFO processing with O(1) per-user pending counts and signature-based duplicate suppression
- **Conversation history persistence** — history is now stored as per-channel files under `bot_data/history_channels/`, writing only dirty channels while still reading legacy `history_cache.json` for migration

### Fixed

- **Version check chatter** — GitHub release lookups now use a 30-minute server-side cache and invalidate after dashboard-triggered updates instead of rechecking on every request
- **Bot fall-off hot path** — bot-to-bot response probability now reads only the fall-off settings it needs instead of copying the full runtime config for every bot message
- **Shutdown/restart history flushing** — restart and shutdown flows now force-save pending history changes so dirty per-channel caches are persisted before the process exits

## [v1.9.1] - 2026-03-21

### Fixed

- **Character switcher selecting the wrong card value** — the Config page now tracks each bot's stable character file key separately from the loaded display name, so mixed-case names like `nahida.md` loading as `Nahida` no longer fall back to the first dropdown option
- **Character switches not persisting the UI key** — switching a bot's character through the dashboard now updates the bot's stored character key as well as the loaded character object, preventing the selector from drifting back after reload

### Added

- Regression coverage for the config page character switcher, including lower-case file stem vs title-case display name cases

## [v1.9.0] - 2026-03-21

### Added

- Bulk auto-memory cleanup tools in the dashboard, including exact row selection, by-user deletion, and scope-targeted deletes for DMs, a chosen server, or all scopes
- Manual dashboard consolidation for targeted auto-memory keys, allowing one-click pruning of noisy per-message memories into a shorter bullet-style list
- Separate bulk user-lore deletion plus regression coverage for targeted delete, filter, and consolidation flows

### Changed

- **Memories dashboard workflow** — the Auto Memories tab now exposes targeted bulk actions directly instead of relying on raw JSON cleanup while the bot is live
- **Memory APIs** — auto-memory and lore endpoints now support scoped filtering and targeted bulk actions for dashboard pruning workflows

### Fixed

- **Live memory cleanup consistency** — dashboard deletes now keep the unified in-memory stores and persisted dedup counters in sync instead of being vulnerable to raw JSON edits being overwritten at runtime
- **Manual pruning gap** — noisy per-message auto memories can now be consolidated on demand for specific users without waiting for the 5-new-memory trigger

## [v1.8.7] - 2026-03-21

### Added

- Regression tests for unified memory persistence, dedup state, and dashboard memory APIs

### Changed

- **Auto Memories dashboard** — now reads from the unified v2 auto-memory API and unified store stats instead of inferring source labels from legacy raw JSON structures
- **Memory dedup contract wording** — documentation and dashboard copy now describe deduplication as running after 5 new auto memories for the same server/user or DM/user key

### Fixed

- **Auto memories misclassified as manual** — unified auto-memory entries now persist `auto: true`, lore persists `auto: false`, and dashboard totals/source labels now reflect the real store
- **Dedup trigger lost across restarts** — pending auto-memory counts are now persisted in `bot_data/memory_state.json` so LLM consolidation still triggers correctly after a restart
- **Concurrent/stale dedup runs** — per-key in-flight guarding and stale-result protection now prevent overlapping LLM dedup tasks from overwriting newer memory state
- **No-embedding fallback behavior** — when embeddings are unavailable, the bot now logs that semantic dedup is disabled for the runtime and continues using fingerprint/text dedup plus LLM consolidation

## [v1.8.6] - 2026-03-20

### Fixed

- **Other bots' messages missing from user-only context** — In user-only mode, ALL bot/app messages from other bots were completely discarded, making replies to other bots and bot-bot conversations nonsensical. Now includes the last 5 messages from each other bot/app in the channel, formatted as user-role entries with author prefix to prevent personality bleed. Current bot's own last 3 assistant turns still kept separately.

## [v1.8.5] - 2026-03-19

### Fixed

- **Multiple tags only producing one reply** — Duplicate detection scanned history for ANY bot response after a user message, falsely concluding the bot already responded to queued messages. When messages A, B, C were queued and A's response appeared after B and C in history, B and C were silently skipped. Replaced fragile history-scanning with a precise set-based tracker of processed message IDs.

## [v1.8.4] - 2026-03-19

### Changed

- **Documentation overhaul** — Comprehensive README.md update to match v1.8.x codebase:
  - Rewrote Memory Architecture section from legacy 5-tier to current 2-store system (auto memories + manual lore)
  - Added 6 missing slash commands to Commands tables (`/switch`, `/pause`, `/nickname-trigger`, `/ignore`, `/unignore`, `/ignorelist`)
  - Added 6 missing runtime config settings (user-only context, DM follow-ups)
  - Updated Dashboard section descriptions (Memories, Config, Logs pages)
  - Expanded File Structure to include all 22 Python modules and commands/ folder
  - Added DM Follow-ups and Scoped Nickname Triggers subsections to Autonomous Mode
  - Updated Features list with user-only context, DM follow-ups, user ignore system, killswitch command
  - Fixed context viewer polling interval reference (3s → 10s)
- Updated CONTRIBUTING.md with project structure notes for new contributors

## [v1.8.3] - 2026-03-19

### Fixed

- **Auto Channels stat counting stale entries** — dashboard stat included channels from `autonomous.json` that bots can no longer access (deleted channels, left servers); now filters against actually accessible channels

## [v1.8.2] - 2026-03-19

### Fixed

- **LLM responding as generic assistant** — `user_only_context` defaulted to `True` in v1.8.0, immediately stripping all assistant turns for all users; LLMs lost roleplay pattern and defaulted to "helpful assistant" mode regardless of system prompt; now defaults to `False` (opt-in via dashboard)
- **User-only mode now keeps last 3 assistant turns** — instead of only 1 (v1.8.1), now keeps the current bot's last 3 responses interleaved chronologically with user messages; provides stronger conversational anchoring while still discarding other bots and older history
- **Synthetic first-turn fallback** — when user-only mode is enabled and the bot has never responded in a channel, injects a synthetic assistant turn from the character's `example_dialogue` to maintain roleplay pattern; skips gracefully if no example dialogue exists
- **Context viewer scroll position preserved** — increased poll interval from 3s to 10s and added content hashing; DOM only updates when context actually changes, preventing mid-scroll jumps

### Changed

- Renamed `context_message_count` to `user_only_context_count` for clarity (default: 20)

## [v1.8.1] - 2026-03-19

### Fixed

- **LLM refusing to roleplay** — user-only context mode was stripping ALL assistant messages, leaving the LLM with zero conversational flow; now keeps the current bot's most recent response as a single assistant turn for anchoring while still discarding all other bot/assistant messages

## [v1.8.0] - 2026-03-19

### Added

- **Autonomous DM follow-ups** — bots send organic follow-up messages in DMs after configurable silence periods; timeout, max count, and cooldown all adjustable from dashboard
- **Scoped nickname triggers** — per-channel toggle (default OFF) for name-based triggers; new `/nickname-trigger` slash command and dashboard toggle in Channels page; bot only responds to @mentions/replies unless explicitly enabled
- **User-only context mode** — based on "Do LLMs Benefit From Their Own Words?" paper; discards ALL bot/assistant messages from LLM context, sending only the last N human user messages; drastically reduces impersonation and context poisoning
- **Unified memory system** — consolidated 5 memory stores (server, DM, user, global profiles, lore) into 2 (auto memories + manual lore); automatic migration from legacy stores on startup with .bak backups
- **LLM-based memory deduplication** — after 5 new auto memories for the same user key, an LLM consolidates and removes redundant entries
- **Manual lore system** — lore can be attached to users, bots, or servers; add/edit/delete via dashboard or `/lore` command with type selection
- **Mass delete for memories and lore** — checkbox selection + "Delete Selected" in dashboard for both auto memories and manual lore
- **New v2 memory API endpoints** — `/api/v2/memories/auto`, `/api/v2/memories/lore` with filtering, batch delete, and add/edit support

### Changed

- **Context system** — `format_history_split()` now supports `user_only` mode that filters out all bot messages; `is_bot` flag added to history entries for filtering
- **Memory commands** — `/memory`, `/memories`, `/lore`, and `/clearmemories` simplified to work with unified 2-store system
- **Dashboard** — Memories page tabs renamed to "Auto Memories" and "Manual Lore"; Config page gets new "User-Only Context" toggle, "Context Message Count" slider, and "DM Follow-ups" section

### Fixed

- **Queue/generation bug** — `add_to_history()` hash dedup now includes `message_id` to prevent different Discord messages with same content from being silently dropped
- **Duplicate detection hardening** — `_build_request_context()` content-based fallback now skipped when message_id is available; `already_in_history` check uses message_id exclusively
- **Per-user pending limit** — increased from 2 to 3 in request queue to prevent legitimate requests from being dropped

## [v1.7.0] - 2026-03-05

### Added

- **OpenRouter first-class support** — auto-detects OpenRouter URLs and injects `HTTP-Referer` and `X-OpenRouter-Title` headers for leaderboard attribution
- **Per-provider `openrouter` config** — optional dict field merged into API request body, supporting provider routing (`order`, `ignore`, `allow_fallbacks`, `sort`, `data_collection`), model fallbacks (`models` array), and context compression (`transforms: ["middle-out"]`)
- **OpenRouter settings in dashboard** — JSON textarea in Edit/Add Provider modals, auto-shown when endpoint URL contains `openrouter.ai`
- **OpenRouter documentation** — new README section with config examples and common options

## [v1.6.19] - 2026-03-05

### Added

- **Per-provider timeout** - Each provider can now have its own `"timeout"` field (5-3600s) that overrides the global timeout. Prevents slow providers (e.g., local LLMs) from being cut off mid-generation while keeping fast cloud APIs on shorter timeouts.
- **Timeout field in dashboard** - Edit Provider modal and Add Provider form now include a timeout input field.
- **Dashboard-first development principle** added to CLAUDE.md.

### Fixed

- **Dashboard dropping global config fields** - `saveProvidersToServer()` was reconstructing JSON as `{ providers: [...] }` only, discarding the global `timeout` and `character_providers` fields. Now preserves all top-level fields when saving via the UI.

## [v1.6.18] - 2026-03-05

### Reverted

- **Hard reset to v1.6.4 baseline** due to stability issues in v1.6.5-v1.6.17
- Removed complex mention/tagging system (`context_protocol.py`, `mention_resolver.py`)
- Removed protocol handle abstraction (@u_123, @b_456 system)
- Removed alias cache persistence (`user_alias_cache.json`)
- Removed history sequence numbers
- Removed cross-store memory deduplication (O(n²) performance issue)
- Removed auto-memory user mention validation and auto-prefixing
- Removed relation-based tag fallback system
- Removed tag intent detection via regex + stopwords
- Removed 11 test files for reverted features

### Added (cherry-picked from v1.6.17)

- **Memory fingerprint-based deduplication** - SHA256 hash-based exact duplicate detection (per-store only)
  - `_memory_fingerprint()` - Generate 24-char hash for each memory
  - `_sanitize_memory_content()` - Normalize content before storage
  - `_sanitize_memory_entries()` - Validate and deduplicate memory lists
  - `_contains_fingerprint()` - Fast fingerprint checking
  - All `add_*_memory()` methods now generate and store fingerprints
  - Fingerprint check added as Stage 0 in `_is_duplicate_memory()` (instant exact match detection)
- **Improved emoji sanitization patterns** for malformed custom emojis:
  - `RE_INCOMPLETE_TAG` - Catches `<:name:id` without closing `>`
  - `RE_MALFORMED_EMOJI_PREFIX` - Catches malformed emoji prefixes
  - Updated `RE_BROKEN_EMOJI_END` - Better incomplete emoji detection (now handles `<a:name:id` patterns)
  - Updated `RE_ORPHAN_SNOWFLAKE` - Avoids false positives on mentions (excludes `@#&/` prefixes)
  - Enhanced `convert_emojis_in_text()` with targeted cleanup and whitespace normalization

### Changed

- Memory deduplication simplified to per-store only (no cross-store checking for performance)
- Emoji cleanup now uses multiple targeted patterns instead of one aggressive regex
- `convert_emojis_in_text()` now handles `None` text input gracefully

### Notes

- This release prioritizes **stability and reliability** over feature richness
- Mention system reverted to v1.6.4 baseline (basic @user pings work, no advanced tagging)
- Context system rework planned for future release with focus on simplicity
- Total lines removed: ~6,000 (from 28 files)
- Total lines added: ~200 (dedup + emoji improvements)

## [v1.6.4] - 2026-02-28

### Fixed

- Disabled `RE_MALFORMED_EMOJI` regex in `convert_emojis_in_text()` — pattern was too aggressive, matching legitimate `<...>` text (up to 50 chars) and truncating messages mid-sentence. `RE_BROKEN_EMOJI_END` still handles incomplete emoji at string end.

## [v1.6.3] - 2026-02-27

### Fixed

- @mention system now includes comprehensive debug logging — logs mentionable user list, lookup table, pattern matches, and conversion results for troubleshooting
- @mention regex now uses lookahead `(?=\W|$)` instead of `\b` word boundary — fixes matching issues with display names containing emojis or special characters
- `get_mentionable_users()` now falls back to guild members when history is sparse (< 3 users) — ensures mentionable list is populated even in new channels or after history clears
- `get_mentionable_users()` now accepts optional `guild` parameter for member fallback — passed from `bot_instance.py` context

## [v1.6.2] - 2026-02-27

### Fixed

- Dashboard memory stats no longer count history cache messages as "manual" memories — `history_cache.json` was not excluded due to filename mismatch in filter
- @mention conversion now handles AI-generated `<@Name>` patterns — normalizes to `@Name` before conversion, preventing malformed `<<@id>>` output
- Bot's own responses now resolve mentions back to display names in history — passes guild context so the AI sees `@DisplayName` instead of generic `@user` in its own past messages

## [v1.6.1] - 2026-02-27

### Fixed

- Dynamic provider tier ordering — tiers beyond "primary/secondary/fallback" now work correctly instead of silently falling back to primary
- @mention replacement handles multi-word display names and no longer strips valid mentions via the safety net
- Bot responds after `/clear` — auto-recall from Discord API is suppressed when history was explicitly cleared
- Impersonation stripping enhanced — catches mid-response `Name:` lines, `*Name says*` roleplay patterns, and sources other bot names from the registry (not just history)
- Reply detection falls back to `fetch_message()` when Discord cache misses, name triggers work without autonomous mode, and duplicate detection uses message IDs instead of content matching
## [v1.6.0] - 2026-02-01

### Added
- Memory card UI - Replace raw JSON textareas with structured memory cards
- Statistics dashboard showing total, auto, and manual memory counts
- Filter controls for memory type, source (Auto/Manual), and search
- Inline memory editing with modal dialog
- Sort memories by newest or oldest first
- Individual memory delete buttons on each card
- Add memory button per file section

### Changed
- Memories page now uses card-based layout instead of table view
- Memory display shows readable badges, timestamps, and metadata
- Improved visual distinction between auto and manual memories

## [v1.5.1] - 2026-02-01

### Added
- Provider edit functionality in web UI (edit button on each provider)
- Support for direct `api_key` in providers.json (in addition to `key_env`)

### Fixed
- Provider test failing with KeyError when using `base_url` instead of `url`
- Backwards compatibility for both `url` and `base_url` in provider config

## [v1.5.0] - 2026-02-01

### Added
- Fallback sanitizer for DMs where guild context is unavailable
- Output safety net to strip raw Discord syntax from AI responses

### Changed
- Sanitize Discord syntax at storage time before sending to LLM (fix `<@id>` leaking to AI)
- Mentionable context now shows `@Username` format instead of raw `<@id>`

### Fixed
- Mobile drag-and-drop for provider reordering (added Sortable.js touch options)

## [v1.4.5] - 2026-01-28

### Changes

- Cap max_tokens for Claude/Opus models to enforce chatroom brevity
- Fix message duplication in LLM context

## [v1.4.4] - 2026-01-28

### Added
- Response sanitization to clean AI output artifacts
- Waitress production server (replaces Flask dev server)
- Provider drag-and-drop reordering in web UI
- `/ignore`, `/unignore`, `/ignorelist` commands for user blocking
- Auto pip install after git pull in dashboard update endpoint
- Error handling and health check for dashboard startup

### Fixed
- Waitress serve() parameter (removed invalid _quiet)

### Reverted
- Response sanitization patterns that caused model identity confusion

## [v1.4.3] - 2026-01-25

### Changes

- Add missing settings UI to dashboard (Mention Settings, Split Replies, Performance)

## [v1.4.2] - 2026-01-25

### Changes

- Fix coordinator asyncio event loop issues (v1.4.2)
## [v1.4.1] - 2026-01-25

### Changes

- Fix systemd restart logic for dashboard update button
## [v1.4.0] - 2026-01-25

### Changes

- Add global coordinator and @mention features
- Fix audioop-lts for Python <3.13
- Bump version to 1.3.3
## [v1.3.3] - 2026-01-21

### Changes

- Fix GLM draft spam leak - extract final response from multiple drafts
- Bump version to 1.3.2
## [v1.3.2] - 2026-01-20

### Changes

- Fix restart under systemd - use systemctl instead of os.execv
## [v1.3.1] - 2026-01-20

### Changes

- Auto-restart after update + fix 5sâ†’10s reload timer
## [v1.3.0] - 2026-01-20

### Changes

- Add split replies feature + fix GLM thinking parameter routing
- Fix dashboard poll interval consistency (5s -> 10s)
## [v1.2.5] - 2026-01-19

### Changes

- Add conservative extended thinking artifact removal
- Change dashboard auto-refresh from 5s to 10s
- Add CLAUDE.md with auto-commit/bump/push instructions
- Add version bumping docs and bump to v1.2.4
## [v1.2.4] - 2026-01-19

Fix interaction followup AttributeError and revert reasoning filters

### Changes

- Fix interaction followup AttributeError and revert reasoning filters
## [v1.2.0] - 2026-01-18

Add git tag creation and auto-changelog to bump script

### Changes
- Add version update indicator and bump script (v1.1.0)
- Add dashboard update button and version display (v1.0.0)
- Replace all fun slash commands with single /interact command
- Fix markdown lint warnings in README
- Improve documentation with fixes and new contributor files
- Add corrupted CJK reference artifact removal
- Improve reasoning filter for structured GLM thinking
- Add plain-text chain-of-thought reasoning filter
- Add banner image with hover preview and GitHub link
- Add slash command interactions to conversation history

## [v1.1.0] - 2025-01-18

### Added

- Dashboard update button to pull latest changes from GitHub
- Version display (v1.1.0) in navbar across all dashboard pages
- `/api/update` endpoint for git pull functionality
- `/api/version` endpoint to check running vs file version
- "New version available" badge after successful update (pulses until restart)
- `version.py` with VERSION constant
- `bump_version.py` script for easy version management

## [v1.0.0] - 2025-01-18

### Changed

- Replace 18 individual slash commands with single `/interact <action>` command
- Process interactions through normal message pipeline for memory generation
- Fix user recognition with fuzzy matching for Discord display names
  - "Kris WaWa" now matches special user "Kris"

### New Features

- Free-form interaction system (`/interact hugs you`, `/interact tells you a secret`)
- SyntheticMessage class to simulate Discord messages from slash commands

### Removed

- `/kiss`, `/hug`, `/bonk`, `/bite`, `/joke`, `/pat`, `/poke`, `/tickle`
- `/slap`, `/cuddle`, `/compliment`, `/roast`, `/fortune`, `/challenge`
- `/holdhands`, `/squish`, `/spank`, `/affection`

## [Unreleased] - Initial Release

### Initial Features

- Initial public release
- Multi-character support with markdown-based character files
- Support for any OpenAI-compatible API (DeepSeek, OpenAI, local LLMs)
- Provider fallback chain with automatic retry
- Vision support with graceful fallback for text-only models
- Web dashboard for managing memories, characters, channels, and config
- 5-tier memory system (global profiles, DM, user, server, lore)
- Auto-memory feature for automatic fact extraction
- Autonomous mode with per-channel configuration
- Bot-on-bot fall-off to prevent infinite conversation loops
- Impersonation prevention for multi-bot setups
- History persistence across restarts
- Rate limit handling with exponential backoff
- Request queue with anti-spam protection
- Provider diagnostics tool (`diagnose.py`)
- Interactive setup scripts for Windows and Mac/Linux
- Docker deployment support
- Prometheus metrics integration

### Documentation

- Comprehensive README with setup instructions
- Character creation templates and examples
- Deployment guides for Windows, Linux, and Docker
