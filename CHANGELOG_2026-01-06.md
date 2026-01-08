# Discord-Pals Changes - 2026-01-06

## Commits

### 1. fix: Add missing CSRF tokens to AJAX calls and dynamic forms (9cbc339)

**Problem:** Dashboard returned `{"error":"Invalid or missing CSRF token"}` (403) when trying to delete/modify data.

**Root Cause:** AJAX `fetch()` calls and dynamically created forms weren't including CSRF tokens.

**Files Modified:**
- `templates/memories.html` - Added `X-CSRF-Token` header to `/api/memories/add` fetch, added CSRF input to delete form
- `templates/channels.html` - Added `X-CSRF-Token` header to `/api/channels/${channelId}/clear` fetch
- `templates/characters.html` - Added CSRF input to `confirmDeleteCharacter()` dynamic form

---

### 2. perf: Implement 8 performance optimizations (58a48ab)

| # | Optimization | File | Description |
|---|-------------|------|-------------|
| 1 | Pre-compiled regex | discord_utils.py | Use existing `RE_EM_DASH_*` patterns in `clean_em_dashes()` |
| 2 | Top-level imports | bot_instance.py | Move `import runtime_config` to module level (was imported 5x inside methods) |
| 3 | Single history fetch | bot_instance.py | `_gather_mentioned_user_context()` now fetches history once for all mentioned users instead of once per user |
| 4 | Cached regex patterns | discord_utils.py | Added `@lru_cache` for character name patterns in `clean_bot_name_prefix()` |
| 5 | LRU emoji cache | discord_utils.py | Added 50-guild limit with LRU eviction to `_emoji_cache` |
| 6 | Hash-based dedup | discord_utils.py | `add_to_history()` uses O(1) hash lookup instead of O(n) iteration for duplicate detection |
| 7 | Lazy legacy loading | memory.py | Legacy memory files (`dm_memories.json`, `user_memories.json`) now lazy-load on first access |
| 8 | Global multipart cleanup | discord_utils.py | Added 5000-entry global limit for `multipart_responses` across all channels |

---

## Notes

### @ Mentions vs Nickname Triggers
The existing behavior is correct:
- **@ mentions** (`@BotNamlways trigger responses regardless of autonomous mode
- **Nickname triggers** (typing bot's name as text) - Only trigger when autonomous mode is enabled for the channel

This is controlled in `bot_instance.py`:
- Line 140: `mentioned = self.client.user in message.mentions` (independent of autonomous)
- Line 163: Nickname check is gated by `channel_id in autonomous_manager.enabled_channels`
