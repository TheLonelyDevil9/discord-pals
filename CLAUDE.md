# Claude Instructions for discord-pals

## After Making Changes

**Always do these steps automatically after completing any code changes:**

1. **Commit** - Stage and commit all changes with a descriptive message
2. **Bump version** - Run `python bump_version.py patch --tag` (use `minor` for new features, `major` for breaking changes)
3. **Push to GitHub** - Push both the commits and the tag:
   ```bash
   git push origin main
   git push origin v<version>
   ```

Do not wait for the user to ask - complete all three steps immediately after any fix or feature is done.

## Version Bump Guidelines

- `patch` - Bug fixes, small changes
- `minor` - New features, non-breaking improvements
- `major` - Breaking changes

## Commit Message Format

Use clear, concise commit messages. End with:
```
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
```
