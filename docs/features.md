# Feature Guide

This guide keeps user-facing behavior out of the root README. Prefer the dashboard for routine setup and maintenance; edit JSON directly only when the dashboard cannot express the change yet.

## Dashboard

The dashboard runs at <http://localhost:5000> when the bot is running.

Main pages:

| Page | Use |
| --- | --- |
| Home | Bot status, pause/resume controls, bot interaction toggle, and global killswitch. |
| Characters | Edit character Markdown, preview parsed sections, inspect prompts, and reload characters. |
| Memories | Search, edit, forget, and clean up learned memories and manual lore. |
| Reminders | Review reminder state and bulk-cancel pending reminders. |
| Channels | Configure autonomous replies, nickname triggers, bot trigger access, cooldowns, and channel history cleanup. |
| Config | Edit runtime settings, providers, schedules, prompt options, response access, import/export, and advanced JSON. |
| Logs & Stats | Watch live logs, message counts, response times, context previews, and recent errors. |

`prompts/system.md` is intentionally read-only in the dashboard. Editable post-system prompt sections live in `prompts/other_prompts.md`.

## Memory And Lore

Discord Pals uses two memory stores:

| Store | Scope | File |
| --- | --- | --- |
| Learned Memories | One profile per server/user or per-bot DM/user scope. | `bot_data/auto_memories.json` |
| Manual Lore | User-created context scoped to server, user, or bot. | `bot_data/manual_lore.json` |

Learned memories are merged into one living profile per scope. If provider cleanup fails, the bot keeps one pending entry for that scope and retries later instead of growing an unmanaged list.

Use the dashboard or slash commands for writes. Raw JSON is available for inspection, but manager-owned stores should not be edited behind the bot's back.

Related commands:

| Command | Use |
| --- | --- |
| `/memory <content> [user_id]` | Save a learned memory. |
| `/memories` | View your learned memories. |
| `/lore [content] [target_type] [target_id]` | Add or view manual lore. |
| `/clearmemories <memory_type> [target_id]` | Clear learned memories or lore. |

## Reminders And Timezones

Reminders are durable scheduled items in `bot_data/reminders.json`. They are separate from DM follow-ups.

- Reminders are created from explicit reminder requests or clear future-planning statements.
- Ambiguous timing should produce a clarification instead of a silent guess.
- The bot you talked to owns the reminder.
- Delivery happens in the same DM/channel when possible, with DM fallback if the source channel is unavailable.
- Final reminder text is generated at send time using current context.

Timezone precedence:

1. User timezone from `/timezone set`.
2. Bot timezone from the dashboard Config page.
3. Process/server timezone.

This precedence applies to reminders and prompt placeholders such as `{{time}}`, `{{date}}`, `{{weekday}}`, and `{{day}}`.

Related commands:

| Command | Use |
| --- | --- |
| `/timezone set <iana_timezone>` | Set your personal timezone. |
| `/timezone show` | Show the active timezone source. |
| `/timezone clear` | Remove your personal timezone override. |
| `/reminders list` | List reminders for the current bot. |
| `/reminders cancel <reminder_id>` | Cancel a pending reminder. |

## Auto Replies And Name Triggers

Auto replies let bots join server conversations without an explicit mention. Configure them per channel from the dashboard Channels page or with `/autonomous`.

Channel settings:

| Setting | Use |
| --- | --- |
| Enabled | Turns autonomous responses on for that channel. |
| Response chance | Probability from 1% to 50%. |
| Cooldown | Minimum time between autonomous replies. |
| Bot triggers | Whether bots/apps can trigger autonomous replies. |
| Nickname trigger | Whether plain-name mentions can wake the bot. |

Nickname triggers are off by default. Use `/nickname-trigger <enabled>` or the dashboard to change them per channel. Discord emoji names are handled so an emoji shortcode does not accidentally trigger a bot nickname.

Custom nicknames are configured per bot. Multi-bot nicknames live in `bots.json`; single-bot nicknames live in runtime config under `bot_nicknames`.

## DM Follow-Ups

DM follow-ups are silence-based nudges in direct messages. They are not reminders and do not create durable scheduled user tasks.

Settings live in the dashboard Config page or `bot_data/runtime_config.json`:

| Setting | Default | Use |
| --- | --- | --- |
| `dm_followup_enabled` | false | Enables follow-ups. |
| `dm_followup_timeout_minutes` | 120 | Silence period before a follow-up. |
| `dm_followup_max_count` | 1 | Max follow-ups before stopping. |
| `dm_followup_cooldown_hours` | 24 | Cooldown for the same user. |

DM random images are an optional branch of the same follow-up loop, so they inherit the DM response switch, DM user blacklist, bot schedule, cooldown, and max-count rules. When enabled, an eligible follow-up can generate one image through configured `image_providers` and send it in the DM, optionally with a short in-character caption.

| Setting | Default | Use |
| --- | --- | --- |
| `dm_image_generation_enabled` | false | Enables generated-image follow-ups. |
| `dm_image_generation_chance` | 0.25 | Chance an eligible follow-up sends an image instead of text. |
| `dm_image_generation_caption_chance` | 0.85 | Chance to include a short generated caption. |
| `dm_image_generation_preferred_tier` | "" | Preferred `image_providers` tier, or default order. |
| `dm_image_generation_prompt` | weird meme prompt | Stable style goal mixed with recent DM context. |

## Bot Availability

Bot schedules define unavailable windows for a bot. Each bot can have multiple windows with selected days, start time, and end time. Overnight windows carry into the next morning.

Schedules are stored in `bot_schedules`, but should normally be edited from the dashboard Config page.

## Bot-To-Bot Fall-Off

The fall-off system reduces bot-to-bot response probability as consecutive bot messages accumulate in a channel. Any human message resets the counter.

Default behavior:

| Consecutive bot messages | Response chance |
| --- | --- |
| 1 | 80% |
| 2 | 65% |
| 3 | 50% |
| 4 | 35% |
| 5 | 20% |
| 6+ | 5% floor |
| 10+ | 0% hard stop |

Use `/stop` or the dashboard Bot Interactions toggle to pause bot-to-bot conversations globally.

## Identity And Context Guardrails

Multi-bot setups inject visible bot names into context so each bot knows not to write as another bot. The identity guard can block generated text that structurally speaks as another bot.

Conversation context keeps the normal history window, preserves the current bot's own assistant turns, and treats other bots as named user-style context so continuity is available without teaching one bot to speak as another.

Response access settings can disable server replies, disable DM replies, enforce a server-channel whitelist, blacklist server channels, or blacklist users from DMs.

## Split Replies And Mentions

When split replies are enabled, the bot can send separate replies to multiple mentioned users instead of one combined message. `split_replies_max_targets` caps the fan-out.

Bot-generated user mentions are allowed by default. Bot-to-bot mentions are disabled by default because they can create loops. Mention candidates come from reply targets, recent participants, visible guild members, and configured limits.

## Characters

Character files live in `characters/`.

Injected sections:

| Section | Behavior |
| --- | --- |
| `## System Persona` | Always included. |
| `## Example Dialogue` | Included when present. |
| `## User Context` | Only the matching `### username` block is included. |

Other second-level headings are ignored by the parser and shown as ignored in the dashboard preview.

## Multi-Bot Setup

The Config page Advanced tab has a Bot Mode panel for switching between single-bot and multi-bot deployments without editing JSON by hand.

Use Single Bot when one Discord application should run from `DISCORD_TOKEN`. Use Multi Bot to add one row per Discord application. Each row writes a `bots.json` entry with the bot name, character, token environment variable, and optional nicknames. Literal Discord tokens stay out of `bots.json`; after saving multi-bot mode, provide each generated token variable through `.env` or the process environment, then restart the bot. The Discord Tokens card can write `.env` values for local installs.

Raw `bots.json` editing remains available in Advanced for recovery and bulk edits.

## Multi-Bot Coordination

With `bots.json`, one process can run multiple Discord applications. The coordinator limits concurrent AI requests and staggers responses when multiple bots answer the same message.

Relevant setting:

| Setting | Default | Use |
| --- | --- | --- |
| `concurrency_limit` | 4 | Max concurrent AI requests across all bots. |

Each bot needs its own Discord application and token variable.
