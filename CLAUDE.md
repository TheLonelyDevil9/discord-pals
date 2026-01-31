# Claude Instructions for discord-pals

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

1. **Bump version FIRST** - Run `python bump_version.py patch --tag` BEFORE committing
   - Use `patch` for bug fixes
   - Use `minor` for new features
   - Use `major` for breaking changes
2. **Update CHANGELOG.md** - Add entry for new version with ALL changes
3. **Commit** - Stage and commit all changes (including version.py and CHANGELOG.md) with a descriptive message
4. **Push to GitHub** - Push both commits and tags:
   ```bash
   git push origin main && git push origin --tags
   ```

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