# Claude Instructions for discord-pals

Agent compatibility entrypoint for `discord-pals`. Keep this file in parity with the paired instruction file when project instructions change.

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

**Always do these steps automatically after completing any code changes:**

1. **Run quality checks** - Run `python tools/quality_check.py` and relevant tests before release steps
2. **Bump version FIRST** - Run `python bump_version.py patch --tag` BEFORE committing
   - Use `patch` for bug fixes
   - Use `minor` for new features
   - Use `major` for breaking changes
3. **Update CHANGELOG.md** - Add entry for new version with ALL changes
4. **Commit** - Stage and commit all changes (including version.py and CHANGELOG.md) with a descriptive message
5. **Publish release** - `bump_version.py --tag` now pushes `main` and the release tag automatically after the release commit is created

**CRITICAL: Version bump MUST happen before commit.** If you commit first, the version.py file won't be included and the release will have the wrong version number.

Do not wait for the user to ask - complete all steps immediately after any fix or feature is done.

## Commit Message Format

Use clear, concise commit messages. Always include the new version number in the commit message or push output so it's visible in the conversation.

Example:
```
Fix restart logic for systemd environments (v1.4.1)
```

## Files to Track for Version Changes

When making changes, ensure these are kept in sync:
- `version.py` - Source of truth for version number
- `CHANGELOG.md` - Human-readable change history
- Git tags - For release tracking
