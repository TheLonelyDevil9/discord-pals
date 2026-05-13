# Discord Pals

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Discord.py](https://img.shields.io/badge/discord.py-2.3.2-7289da)

Discord Pals is a local Discord bot project for running one or more character bots from Markdown character files. It works with OpenAI-compatible cloud providers, local LLM servers, fallback provider chains, image-aware models, reminders, memory, and a browser dashboard.

It is heavily inspired by SpicyMarinara's [Discord Buddy](https://github.com/SpicyMarinara/Discord-Buddy). The system prompt was authored by Geechan.

<p align="center">
  <img src="images/banner.jpg" alt="Discord Pals" width="1200">
</p>

## Start Here

- [Provider Configuration](docs/provider-config.md) covers `providers.json`, local models, fallback chains, reasoning options, vision support, and OpenRouter.
- [Feature Guide](docs/features.md) covers the dashboard, commands, memory, reminders, auto replies, characters, and multi-bot behavior.
- [Runtime Configuration](docs/runtime-config.md) lists the live settings in `bot_data/runtime_config.json`.
- [Operations](docs/operations.md) covers dashboard security, updates, deployment, file layout, and troubleshooting.
- [Engineering Map](docs/README.md) is for maintainers and coding agents.

## Features

- Markdown character files with persona, example dialogue, and per-user context blocks.
- OpenAI-compatible provider support, including local LLM servers and fallback tiers.
- Vision-capable request handling with text-only fallback for non-vision models.
- Web dashboard for characters, prompts, providers, runtime settings, memories, reminders, channels, logs, stats, updates, and restart controls.
- Unified learned-memory and manual-lore stores with dashboard editing.
- Durable reminders, user and bot timezones, and optional autonomous DM follow-ups.
- Multi-bot mode from one process, with global request coordination and bot-to-bot fall-off.
- Bot identity guardrails, user-only context mode, response access controls, mention handling, split replies, and user ignore commands.
- Built-in diagnostics, updater, setup scripts, quality checks, and production-friendly dashboard serving.

## Requirements

- Python 3.10 or newer. Python 3.11 or 3.12 is the safest default; Python 3.13+ is supported through `audioop-lts`.
- A Discord bot token.
- At least one OpenAI-compatible AI provider, either cloud-hosted or local.

Install dependencies with:

```bash
pip install -r requirements.txt
```

## Quick Start

Clone the repo:

```bash
git clone https://github.com/TheLonelyDevil9/discord-pals.git
cd discord-pals
```

Run the interactive setup:

```bash
# Windows: double-click setup.bat

# macOS/Linux:
chmod +x setup.sh
./setup.sh
```

The setup script creates a virtual environment, installs dependencies, asks for provider and bot details, and writes `.env`, `providers.json`, and `bots.json` when needed.

Create or update your Discord application:

1. Open the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create an application, open its Bot page, and copy the bot token.
3. Enable Presence Intent, Server Members Intent, and Message Content Intent.
4. In OAuth2 URL Generator, select `bot` and `applications.commands`.
5. Invite the bot with the channel/message permissions it needs: view channels, send messages, read message history, add reactions, use external emojis, and manage messages if you want cleanup commands.

Add secrets to `.env`:

```env
DISCORD_TOKEN=your_discord_bot_token
OPENAI_API_KEY=your_openai_key
OPENROUTER_API_KEY=your_openrouter_key
DEEPSEEK_API_KEY=your_deepseek_key
LOCAL_API_KEY=optional
```

For provider examples, see [Provider Configuration](docs/provider-config.md).

Run the bot:

```bash
# Windows: double-click run.bat

# macOS/Linux:
chmod +x run.sh
./run.sh
```

You can also run directly:

```bash
python main.py
```

When startup succeeds, the console shows the loaded character, synced slash commands, and the bot's online event.

## Dashboard

The dashboard starts with the bot at <http://localhost:5000>. It binds to `0.0.0.0:5000`, so protect it before exposing that port outside a trusted machine or private network:

```env
DASHBOARD_USER=admin
DASHBOARD_PASS=your_secure_password
```

Use the dashboard first for routine edits:

- Characters and prompt previews.
- Provider order, provider definitions, and per-character provider tiers.
- Runtime config, response access controls, bot schedules, nicknames, and automation.
- Memories, manual lore, reminder queue, channel auto-reply settings, logs, stats, updates, and restart actions.

## Common Commands

Grouped commands appear in Discord as nested slash commands such as `/timezone set` and `/reminders list`.

| Command | Use |
| --- | --- |
| `/status` | Check bot and provider status. |
| `/reload` | Reload the current character file. |
| `/switch [character]` | Switch characters or list available characters. |
| `/history clear` | Clear this chat's persisted conversation history. |
| `/recall [count]` | Pull recent Discord messages into context, up to 200. |
| `/memory <content>` | Save a learned memory. |
| `/memories` | Show your learned memories. |
| `/lore` | Add or view server, user, or bot lore. |
| `/timezone set/show/clear` | Manage your personal timezone. |
| `/reminders list/cancel` | Review or cancel reminders for the current bot. |
| `/ignore`, `/unignore`, `/ignorelist` | Control which bots respond to you. |
| `/interact <action>` | Send a free-form interaction through the normal reply pipeline. |
| `/autonomous` | Owner-only channel auto-reply toggle. |
| `/nickname-trigger` | Owner-only channel nickname trigger toggle. |
| `/stop` | Pause or resume bot-to-bot conversations. |
| `/pause` | Owner-only global killswitch. |
| `/delete_messages` | Delete recent messages from the current bot. |

See [Feature Guide](docs/features.md) for behavior details.

## Characters

Characters live in `characters/*.md`:

```markdown
# Character Name

## System Persona

Write the character's personality, backstory, appearance, voice, and boundaries.

## Example Dialogue

`{{user}}`: Hello.
`Character`: "Hey. I was wondering when you'd show up."

## User Context

### yourdiscordname

Special context for this exact Discord username.
```

Only `System Persona`, `Example Dialogue`, and matching `User Context` blocks are injected. Other second-level sections are ignored by the parser and shown as ignored in the dashboard preview.

Per-character provider selection is managed in the dashboard Config page, not inside character Markdown.

## Multi-Bot Mode

Run multiple bots from one process with `bots.json`:

```json
{
  "bots": [
    {"name": "Firefly", "token_env": "FIREFLY_TOKEN", "character": "firefly"},
    {"name": "Nahida", "token_env": "NAHIDA_TOKEN", "character": "nahida"}
  ]
}
```

Add matching token variables to `.env`:

```env
FIREFLY_TOKEN=your_firefly_token
NAHIDA_TOKEN=your_nahida_token
```

Each bot needs its own Discord application. Startup validation reports missing or placeholder token variables before launch.

## Data And Local Files

- Runtime state lives under `bot_data/`.
- Character files live under `characters/`.
- Prompt files live under `prompts/`; `prompts/system.md` is read-only in the dashboard, and `prompts/other_prompts.md` holds editable post-system prompt sections.
- Provider config lives in `providers.json`.
- Multi-bot config lives in `bots.json`.
- Tokens belong in `.env`; do not commit real secrets.

## Troubleshooting

- Missing token: single-bot mode needs `DISCORD_TOKEN`; multi-bot mode needs every `token_env` from `bots.json`.
- Bot online but silent: enable Message Content Intent and check response access settings in the dashboard.
- Slash commands missing: restart the bot, confirm the invite used `applications.commands`, and check the dashboard Slash Command Sync section.
- Provider errors: run `python diagnose.py`, check API keys, and increase provider timeout for slow local models.
- Character edits not active: use `/reload` or the dashboard reload action.
- Dashboard unreachable: open <http://localhost:5000> on the host machine; check firewall/reverse-proxy settings for remote access.
- Too chatty: disable autonomous mode for that channel, lower `name_trigger_chance`, or use `/stop` for bot-to-bot loops.

More fixes are in [Operations](docs/operations.md).

## Credits

Made by TLD and collaborators. Credits to SpicyMarinara for the original Discord Buddy inspiration and Geechan for the project name and system prompt.
