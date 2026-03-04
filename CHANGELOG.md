# Changelog

All notable changes to Discord Pals will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [v1.6.12] - 2026-03-04

### Added

- Pre-resolve explicitly requested tag targets into `mentionable_users` before model context building — when a user says "tag febs", the target is now resolved from guild members and injected at the top of the mention list so the model uses the correct handle instead of guessing from recent posters
- New `_pre_resolve_tag_targets()` method on BotInstance with three-tier guild search (local cache → `query_members` → `fetch_members`) matching the same alias-variant logic used by the post-generation failsafe
## [v1.6.11] - 2026-03-04

### Fixed

- Mention resolver now finds members by display name/nick when `guild.query_members()` misses them (it only matches usernames without presences intent) — the `fetch_members` fallback was gated behind an early return that skipped it when other terms had results
- Added missing stopwords (`for`, `this`, `that`, `its`, `with`, `from`, `not`, `but`, `just`, `like`) to prevent wasteful guild member queries on common words

### Added

- Debug logging when a request term fails to resolve in `resolve_mentions_unified()`
## [v1.6.10] - 2026-03-04

### Fixed

- Custom emojis with missing closing `>` (e.g. `<:cute:id`) now normalize correctly instead of leaking as plaintext
- `@word` mention patterns no longer leak to Discord when `allow_mentions` is disabled — `strip_unresolved_plain_mentions()` now runs unconditionally

### Added

- `RE_MALFORMED_EMOJI_PREFIX` safety-net pattern in `discord_utils.py` to catch malformed emoji in `convert_emojis_in_text()` pre-pass
## [v1.6.9] - 2026-03-04

### Changes

- Fix incomplete Discord mention and emoji tag parsing
- Fix tag target extraction with leading reply mentions (v1.6.8)
- Fix mention tagging consistency and bump version to 1.6.7
## [v1.6.8] - 2026-03-04

### Changes

- Fix tag-target parsing so leading reply mentions (for example `@Kris ... tag febs`)
  no longer override the requested target
- Add regression coverage for leading-mention tag requests to keep tagging deterministic
## [v1.6.7] - 2026-03-04

### Changes

- Harden user tagging pipeline with strict no-ambiguity mention resolution (`no_tag`)
- Always apply explicit tag-request fallback so requested mentions are injected reliably
- Add durable per-guild alias cache to resolve users not currently in active context
- Strip unresolved plaintext `@...` artifacts while preserving valid Discord `<@id>` mentions
- Add mention sanitizer regression tests for conversational `@word` leak cases

## [v1.6.6] - 2026-03-04

### Changes

- Add clear-all option that preserves lore
## [v1.6.5] - 2026-03-04

### Changes

- Stabilize per-request context and harden anti-impersonation
- Fix tagging consistency, thread triggers, auto-memory, and unignore matching
- Unify mention resolution for consistent user and bot tagging
- Restore reliable auto-memory generation and successful-save cooldown
- Harden tag intent resolution for off-context members
- Add relation-based tag fallback and normalize raw emoji IDs
- Fix auto-memory rejection for partial display-name outputs
- Improve explicit tag fallback for bot and punctuation variants
- Add dashboard button to clear all memories and profiles
- Resolve tag requests via guild lookup and coerce numeric YAML params
- Inject requested mentions when user asks to tag someone
- Fix protocol-handle fallback when envelope is sparse
- Convert protocol handles via guild member fallback
- Force-convert trusted protocol handles before send
- Fix unresolved protocol handle mentions before send
- Rework context pipeline with deterministic mention handles
- Resolve short-name @mentions to real Discord user tags
- Enforce @Name mention style in context and send path
- Add hard final cleanup for malformed mention stubs
- Fix speaker leakage and offline mention resolution
- Strip user-attribution output and remove system prompt guard edits
- Fix multi-message reply tracking and queue burst handling
- Fix plaintext @user mention fallback for guild members
- Harden bot mention sanitization and bot discovery fallback
- Fix malformed '<@ Name' mention normalization
- Fix multibot timeout, mention handling, clear-state, and memory dedupe
- Disable aggressive RE_MALFORMED_EMOJI regex that truncated messages (v1.6.4)
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
