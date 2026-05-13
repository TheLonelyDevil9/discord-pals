# Discord Pals History Lessons

Created for The_Lonely_Devil / TLD.

## Scope

- Repository: `TheLonelyDevil9/discord-pals`
- Ref mined: `codex/prose-polisher-expanded-prompt`
- Latest hash reviewed: `a4fd232`
- Latest reviewed date: 2026-05-08
- Commit count reviewed: 293
- Main authorship shape: mostly TLD99 implementation commits, with Geechan-authored prompt/template updates mixed into the runtime history.

## History Shape

Discord Pals has grown through fast operational releases, often fixing live Discord behavior immediately after feature work. The repeated hotspots are `bot_instance.py`, `dashboard.py`, `discord_utils.py`, `runtime_config.py`, prompt files, templates, and tests. The most common failure mode is not a missing feature; it is a boundary mismatch between Discord events, model context, provider output, dashboard state, and persisted history.

## Durable Lessons

### 1. Treat response delivery as a boundary, not a prompt preference.

Evidence: `650c513` fixed newline response splitting, `5d717e7` fixed response splitting alongside protected memory endpoints, `13b814f` fixed response splitting punctuation, and `a4fd232` extracted final delivery formatting after another grammar/line-break issue.

Guardrail: Generated text must be normalized into explicit Discord message parts after sanitization/polishing and before send/history write. Cover screenshot-like transcripts, short greetings, missing punctuation, explicit newlines, dash-started lines, compact replies, abbreviations such as `Mr.`, and conjunction false positives. Do not rely on prompt prose to guarantee line breaks.

### 2. Identity isolation must separate raw Discord routing from model context.

Evidence: `c99e381` fixed impersonation among several Discord bugs, `bbae600` adjusted user-only context and roleplay anchoring, `d9e0563` changed user-only context behavior, and `698bd0f` hardened bot identity isolation with neutral bot references and a post-generation identity guard.

Guardrail: When changing routing, replies, history formatting, or model context, verify all four cases: human replying to bot, bot replying to bot, human replying to human, and human referencing prior human text without a reply. Raw events may wake a bot, but other-bot prose should not silently enter model context in hardened mode. Block unsafe structural attribution before Discord send and before history write.

### 3. Dashboard-backed settings need backend schema, UI, tests, and persistence together.

Evidence: `c120a4e` fixed dashboard saves dropping global config fields, `cb8af17` fixed prompt propagation and config migration, `a655cf8` added runtime config validation and quality checks, `92546ee` added post-system prompts with config UX, and `698bd0f` added identity guard settings through runtime config and dashboard UI.

Guardrail: New runtime behavior should not be backend-only. Add defaults, config field validation, dashboard controls, malformed payload tests, persistence tests, and next-request behavior tests in one change. Keep `runtime_config.DEFAULTS` and `CONFIG_FIELDS` aligned.

### 4. Updater/version behavior is operationally fragile; test remote, tag, release, and local checkout states.

Evidence: `2147820` fixed dashboard prompt saves and updater resilience, followed by `d9af7dc`, `1b3d850`, and `a2009b7` in consecutive releases to harden updater verification, semantic latest-version resolution, peeled tags, and remote tag discovery.

Guardrail: Before touching dashboard updates or version checks, test stale GitHub API data, fresh tags, annotated tags, local branch ahead/behind states, dirty working trees, restart-required states, and failed update verification. Release changes must update `version.py`, `CHANGELOG.md`, and tags together.

### 5. Provider and reasoning-output cleanup is high risk for both leaks and over-sanitization.

Evidence: `e77e8ef` fixed reasoning model content leaks, `f24b748` fixed GLM thinking-process leakage, `33c4346` fixed GLM draft spam leaks, and `b570f2b` reverted response sanitization patterns that confused the model.

Guardrail: Sanitizers should target concrete artifacts with tests from real provider output. Avoid broad text rewrites that can teach or confuse the model. Validate both sides: leaked reasoning/draft text is removed, while normal prose, Discord mentions, custom emoji, and character cadence survive.

### 6. Conversation scope identifiers are easy to cross-wire.

Evidence: `a6273e1` fixed `/interact` cross-thread targeting leakage, `ae24002` fixed DM isolation and scheduling controls, `443449a` stabilized scoped runtime identifiers, and `f6a512f` stabilized request routing/send state handling.

Guardrail: Any change touching DMs, `/interact`, reminders, split replies, memory, or queued requests must verify channel/server/user/history identifiers. Tests should prove that one request cannot write to another channel, user, DM, thread, or bot history.

### 7. Dashboard security fixes recur around CSRF and protected endpoints.

Evidence: `d64faa9` fixed missing CSRF tokens in dashboard API calls, `f3d0020` fixed character provider switching CSRF, and `5d717e7` protected memory endpoints while adding tests.

Guardrail: Every authenticated dashboard write route needs CSRF handling and tests. When adding buttons, forms, fetch calls, or editor endpoints, verify both success and rejected malformed/unauthenticated requests.

### 8. Async Discord lifecycle bugs often appear after "small" behavioral changes.

Evidence: `284c9c4` fixed a race that stopped the bot after duplicate skips, `9a00067` fixed coordinator event-loop issues, `f6a512f` stabilized request routing and send state, and `4bea547` changed provider failure notices to avoid noisy public behavior.

Guardrail: Changes around queues, duplicate detection, circuit breakers, send failure handling, typing indicators, or background tasks need tests for partial sends, send failures, duplicate skips, cancellation, and history side effects. Failed sends must not create phantom assistant history.

### 9. Prompt files are authored content; code should adapt around them.

Evidence: Geechan prompt/template commits (`de7703a`, `42a132c`, `6aca9b4`, `a48b65d`) coexist with code fixes like `9d1bd67` for prompt parsing and Prose Polisher fallback behavior.

Guardrail: Do not casually rewrite Geechan-authored prompt prose to fix runtime bugs. Prefer code-owned parsing, context assembly, validation, and output guards. When prompt structure changes, test nested headings, placeholders, XML/Markdown forms, and missing-placeholder fallbacks.

### 10. Reviews and release gates must happen before push/tag, not as an afterthought.

Evidence: The `v2.2.7` delivery formatter was initially pushed before the required 4SR pass; the belated review found a dash-started newline regression and required amending the release commit/tag.

Guardrail: Match review depth to risk before final handoff. Use the full 4SR review flow before pushing release tags, PR-bound behavior changes, broad refactors, or user-requested review passes. For narrow local fixes, do a focused self-review plus relevant tests. Treat review as part of implementation, not a postscript. If a review finds an issue after push, fix it, rerun focused and full tests, then clearly report any amended or force-updated refs.

## Agent-First Checks

- Read this file before planning edits.
- Read `docs/README.md`, then the specific deeper doc for the touched area.
- For code changes, run focused tests first, then `python tools/quality_check.py`, then `python -m pytest`.
- For release-bound or high-risk code contributions, perform the user's 4SR review flow before final handoff; for narrow local fixes, perform a focused review and tests.
- For response/context changes, add transcript-style regression tests rather than only helper-unit tests.
- For dashboard settings, test dashboard payload validation and runtime effect.
- For release work, keep `version.py`, `CHANGELOG.md`, commit, and tag synchronized.

## Operating Principle

Discord Pals failures usually happen at boundaries: Discord event to history, history to prompt, provider output to Discord send, dashboard JSON to runtime config, and release metadata to updater state. Make those boundaries explicit, tested, and reviewable.
