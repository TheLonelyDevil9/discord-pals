# Changes Made in v1.4.x Session

## v1.4.0 - Multi-Bot Coordination & Mention Features

### Feature 1: Global Coordinator (Crash Fix)
**Problem:** When multiple bots were tagged simultaneously, they all responded at once causing crashes.

**Solution:** Created `coordinator.py` with `GlobalCoordinator` class:
- Uses asyncio semaphore to limit concurrent AI requests (uses `concurrency_limit` setting, default 4)
- Staggers responses by 1.5 seconds per bot when multiple bots respond to same message
- Integrated into `bot_instance.py` `_process_request()` method

### Feature 2: Bot @Mentions for Users
**Problem:** Bots couldn't tag humans with actual Discord @mentions in autonomous mode.

**Solution:**
- Added `user_id` parameter to `add_to_history()` in `discord_utils.py`
- Created `get_mentionable_users()` to extract users from conversation history
- Created `process_outgoing_mentions()` to convert `@Name` → `<@user_id>` in responses
- Added mentionable users context to AI prompts via `character.py`
- New config: `allow_bot_mentions: true`

### Feature 3: Bot-to-Bot @Mentions
**Problem:** Bots couldn't summon other bots with @tags.

**Solution:**
- Created bot registry (`register_bot()`, `get_other_bots_mentionable()`)
- Bots register themselves on ready
- AI receives context about other bots it can mention
- New config: `allow_bot_to_bot_mentions: false` (disabled by default due to loop risk)

### Files Created
- `coordinator.py` - Global request coordinator

### Files Modified
- `bot_instance.py` - Integrated coordinator, added user_id to history, bot registration, mention processing
- `discord_utils.py` - Added user_id to history, bot registry, mention functions
- `character.py` - Added mentionable context to `build_chatroom_context()`
- `prompts/chatroom_context.md` - Added `{{MENTIONABLE_USERS}}` and `{{MENTIONABLE_BOTS}}` placeholders
- `runtime_config.py` - Added mention config options

### New Config Options
```python
"allow_bot_mentions": True,           # Bots can @mention users
"allow_bot_to_bot_mentions": False,   # Bots can @mention other bots
"mention_context_limit": 10,          # Max users shown in context
"concurrency_limit": 4,               # Max concurrent AI requests (already existed, now used)
```

---

## v1.4.1 - Dashboard Restart Fix

### Problem
Dashboard "Update" button would pull code but restart didn't work on systemd (Oracle Cloud).

### Root Cause
- Strict `INVOCATION_ID` check for systemd detection
- `sudo systemctl restart` required passwordless sudo
- `os.execv()` from daemon thread failed silently

### Solution
Modified `dashboard.py` `do_restart()`:
- Simplified systemd detection (just check `/run/systemd/system`)
- Try multiple restart commands in order
- Fall back to SIGTERM (systemd auto-restarts if `Restart=always`)

---

## v1.4.2 (pending) - Coordinator Event Loop Fix

### Problem
Crashes still occurring with `KeyboardInterrupt` / `CancelledError` in asyncio.

### Root Cause
`asyncio.Lock()` was created at module import time, before event loop started.

### Solution
- Made `_request_lock` lazily initialized via `_get_lock()` method
- Added try/except around all coordinator methods to prevent crashes
- If coordinator fails, bot continues anyway (graceful degradation)

---

## CLAUDE.md Update
Added requirement to include version number in commit messages.
