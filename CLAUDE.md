# Claude Instructions for discord-pals

Agent compatibility entrypoint for `discord-pals`. Keep this file in parity with the paired instruction file when project instructions change.

Follow the global AI-stack standards hub first: `C:\Users\TheLonelyDevil\.codex\AI_STACK_STANDARDS.md`. Workspace-only routing lives in `D:\AIStuff\AGENTS.md`.

## Repository Map

Start with [docs/README.md](docs/README.md), then open only the deeper document that matches the task:

- [docs/architecture.md](docs/architecture.md) for ownership, runtime domains, and boundary rules.
- [docs/quality.md](docs/quality.md) for mechanical checks and review invariants.
- [docs/agent-workflow.md](docs/agent-workflow.md) for the expected Codex change loop.
- [docs/plans/README.md](docs/plans/README.md) for long-running refactor plans.

## Development Principles

**Dashboard-first:** All features must be built with the web dashboard in mind. New configuration options, runtime settings, or data structures should be viewable and editable through the dashboard UI. Don't add backend-only config that requires manual JSON editing.

**Parse at boundaries:** Dashboard JSON, config files, provider responses, and imported archives should be normalized before hot-path code uses them.

**Guardrails over style:** Prefer small, enforceable checks and focused tests over long prose rules.

Before planning or editing, read [lessons.md](lessons.md) and apply its history-derived guardrails; do not repeat known project mistakes.

## Version Bump Checklist

**When bumping a version, ALL of these must be updated together:**

1. **version.py** - Update `__version__` string
2. **CHANGELOG.md** - Add new version section with:
   - Version number and date
   - Categorized changes (Added, Changed, Fixed, Removed, Reverted)
   - All changes since last version
3. **README.md** - Update any version references if applicable
4. **Git tag** - Create tag matching version (e.g., `v1.4.5`)

## After Making Changes

For ordinary implementation work, run the lightest checks that prove the touched path:

1. Run `python tools/quality_check.py` when Python/dashboard/backend behavior changed.
2. Run the relevant focused tests or explain why no focused test exists.
3. Review the diff for dashboard coverage, config parsing, and lessons-derived guardrails before handoff.

Release steps are separate. Only bump versions, update release changelog sections, create tags, push `main`, or publish a release when the task is explicitly a release/shipping task or the user asks for that workflow.

When doing a release, the version bump must happen before the release commit so `version.py` and `CHANGELOG.md` are included together.

## Commit Message Format

Use clear, concise commit messages. For release commits, include the new version number in the commit message or push output so it's visible in the conversation.

Example:
```
Fix restart logic for systemd environments (v1.4.1)
```

## Files to Track for Version Changes

When making changes, ensure these are kept in sync:
- `version.py` - Source of truth for version number
- `CHANGELOG.md` - Human-readable change history
- Git tags - For release tracking
