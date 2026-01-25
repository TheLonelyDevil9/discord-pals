# Claude Instructions for discord-pals

## After Making Changes

**Always do these steps automatically after completing any code changes:**

1. **Bump version FIRST** - Run `python bump_version.py patch --tag` BEFORE committing
   - Use `patch` for bug fixes
   - Use `minor` for new features
   - Use `major` for breaking changes
2. **Commit** - Stage and commit all changes (including the bumped version.py) with a descriptive message
3. **Push to GitHub** - Push both commits and tags:
   ```bash
   git push origin main && git push origin --tags
   ```

**CRITICAL: Version bump MUST happen before commit.** If you commit first, the version.py file won't be included and the release will have the wrong version number.

Do not wait for the user to ask - complete all three steps immediately after any fix or feature is done.

## Commit Message Format

Use clear, concise commit messages. Always include the new version number in the commit message or push output so it's visible in the conversation.

Example:
```
Fix restart logic for systemd environments (v1.4.1)
```