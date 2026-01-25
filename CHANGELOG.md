# Changelog

All notable changes to Discord Pals will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
