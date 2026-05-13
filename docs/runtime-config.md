# Runtime Configuration

Runtime settings live in `bot_data/runtime_config.json` and are editable through the dashboard Config page. Changes apply without restarting unless the dashboard says otherwise.

The dashboard is the preferred editor because it validates common shapes and keeps in-memory state aligned. Manual JSON edits are best reserved for recovery or bulk maintenance while the bot is stopped.

## Prompting And Context Files

- `prompts/system.md` is the fixed system prompt surface and is read-only from the dashboard.
- `prompts/other_prompts.md` contains editable post-system sections, including chatroom context, reminder delivery context, DM follow-up framing, time-passage context, and Prose Polisher text.

## Settings

| Setting | Default | Use |
| --- | --- | --- |
| `history_limit` | 200 | Max messages included in normal AI context. |
| `immediate_message_count` | 5 | Newest messages placed after post-system context for extra recency weight. |
| `active_provider` | null | Reserved dashboard field; provider order and per-character preferences control generation. |
| `bot_interactions_paused` | false | Pauses bot-to-bot conversations. |
| `global_paused` | false | Global killswitch for bot responses. |
| `server_responses_enabled` | true | Allows normal server-channel replies. |
| `dm_responses_enabled` | true | Allows DM replies, DM follow-ups, and server-to-DM delivery. |
| `response_channel_whitelist_only` | false | Restricts server replies to whitelisted channels. |
| `response_channel_whitelist` | [] | Server channel IDs allowed in whitelist-only mode. |
| `response_channel_blacklist` | [] | Server channel IDs where replies are blocked. |
| `dm_user_blacklist` | [] | User IDs blocked from DM replies and follow-ups. |
| `use_single_user` | false | Sends SillyTavern-style single-user payloads for compatible providers. |
| `prose_polisher_enabled` | false | Runs an optional post-generation cleanup pass. |
| `prose_polisher_max_tokens` | 8192 | Token cap for the Prose Polisher pass. |
| `prose_polisher_preferred_tier` | "" | Optional provider tier for polishing. |
| `name_trigger_chance` | 1.0 | Probability for plain-name/nickname triggers. |
| `custom_nicknames` | "" | Legacy global nickname field. Prefer dashboard-managed per-bot nicknames. |
| `bot_nicknames` | {} | Single-bot nickname map persisted by dashboard controls. |
| `raw_generation_logging` | false | Logs raw model output before processing. |
| `diagnostic_logging` | false | Enables high-volume structured diagnostics. |
| `file_logging_enabled` | true | Writes local JSONL logs under `bot_data/logs`. |
| `log_file_max_mb` | 10 | Max JSONL log size before rotation. |
| `bot_timezones` | {} | Per-bot timezone fallback map. |
| `bot_schedules` | {} | Per-bot unavailable-window schedules. |
| `bot_falloff_enabled` | true | Enables bot-to-bot probability decay. |
| `bot_falloff_base_chance` | 0.8 | Starting probability for the first bot response. |
| `bot_falloff_decay_rate` | 0.15 | Probability reduction per consecutive bot message. |
| `bot_falloff_min_chance` | 0.05 | Probability floor before hard limit. |
| `bot_falloff_hard_limit` | 10 | Consecutive bot-message hard stop. |
| `split_replies_enabled` | false | Enables separate replies to multiple mentioned users. |
| `split_replies_max_targets` | 5 | Max split-reply targets. |
| `concurrency_limit` | 4 | Max concurrent AI requests across all bots. |
| `allow_bot_mentions` | true | Lets bots mention users in replies. |
| `allow_bot_to_bot_mentions` | false | Lets bots mention other bots. Use carefully. |
| `mention_context_limit` | 10 | Max mention candidates shown to the model. |
| `user_only_context` | false | Mostly sends human messages to reduce bot voice bleed. |
| `user_only_context_count` | 20 | Human-message count for user-only mode. |
| `strict_human_only_context` | true | Excludes bot/assistant prose from model history in user-only mode. |
| `identity_guard_enabled` | true | Blocks generated text that structurally speaks as another bot. |
| `identity_guard_policy` | `regenerate_then_drop` | Either regenerate once then drop, or drop immediately. |
| `bot_reference_context_mode` | `neutral` | Uses neutral metadata for referenced bot prose. `legacy` preserves older behavior. |
| `time_passage_context_enabled` | true | Adds elapsed-time cues after long chat gaps. |
| `dm_followup_enabled` | false | Enables autonomous DM follow-ups after silence. |
| `dm_followup_timeout_minutes` | 120 | Silence period before a DM follow-up. |
| `dm_followup_max_count` | 1 | Max follow-ups before stopping. |
| `dm_followup_cooldown_hours` | 24 | Cooldown for the same user. |

## Practical Adjustments

Slow responses:

- Lower `history_limit`.
- Lower provider timeout only for fast cloud providers; local models usually need more time.

Missing context:

- Raise `history_limit`.
- Raise `immediate_message_count` within reason.
- Use `/recall` to import recent Discord messages.

Too many responses:

- Disable autonomous mode in the channel.
- Lower `name_trigger_chance`.
- Disable nickname triggers.
- Add channels or users to response access controls.

Bot voice bleed:

- Enable `user_only_context`.
- Keep `strict_human_only_context` enabled.
- Keep `identity_guard_enabled` enabled in multi-bot channels.

Debugging:

- Use `raw_generation_logging` only temporarily.
- Use `diagnostic_logging` for lifecycle traces, then turn it off.
- Keep file logging enabled if you need post-crash evidence.
