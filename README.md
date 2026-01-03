# Discord Pals

Heavily inspired by SpicyMarinara's [Discord Buddy](https://github.com/SpicyMarinara/Discord-Buddy) repo.

Her tool was so easy to make work, it was amazing.

This is a modified version of Discord Buddy, called Discord Pals, which is a templatized Discord bot that can roleplay as any character loaded from simple markdown files. Supports cloud AI providers (OpenAI-compatible APIs work, DeepSeek, etc.) or your own local LLM.

The system instructions were authored by legendary chef @Geechan.

<p align="center">
  <img src="images/banner.jpg" alt="Discord Pals" width="1200">
</p>

## Features

- **Any character** - Load characters from markdown files with persona, example dialogue, and special user contexts
- **Plug-and-play AI providers** - Configure via JSON, no code changes
- **Local LLM support** - Use llama.cpp, Ollama, LM Studio, or any OpenAI-compatible API
- **Provider fallback** - Auto-retry with backup providers if one fails
- **Rate limit handling** - Automatic retry with exponential backoff on 429 errors
- **Web dashboard** - Full web UI for managing:
  - Memories and lore editing (with user name resolution)
  - Character files and live preview
  - System prompts with placeholder reference
  - Runtime config (history limit, batch timeout, provider switching)
  - Context visualization with token estimates
  - Message stats (daily counts, response times, top users)
  - Live log streaming
  - Autonomous channel monitoring
- **Character hot-swap** - Switch characters via dashboard or `/switch` command
- **Multi-bot support** - Run multiple bots from a single terminal/process
- **Memory system** - Per-character isolation with cross-server user profiles
- **Auto-memory** - Automatically remembers important facts from conversations
- **History persistence** - Conversation history survives restarts
- **Mention-triggered context** - Gathers ephemeral context about mentioned users without storing
- **Message batching** - Collects follow-up messages before responding (configurable timeout)
- **Bot-bot control** - `/stop` command to pause bot-to-bot reply chains globally
- **Context-aware commands** - Slash commands use chat history and memories
- **18 fun commands** - `/kiss`, `/hug`, `/bonk`, `/cuddle`, `/roast`, `/affection`, and more
- **Smart responses** - Tracks reply chains with full message context
- **Anti-spam** - Request queue with rate limiting built-in
- **History recall** - Recover context with `/recall` (up to 200 messages)
- **Autonomous mode** - Bot randomly joins conversations (configurable per-channel)
  - Name triggers (responding to nickname mentions) are now a subset of autonomous mode
  - Per-channel control over whether bots/apps can trigger responses
  - Configurable response chance and cooldown per channel

---

## Requirements

1. **Python 3.10+** - [Download here](https://www.python.org/downloads/)
2. **A Discord Bot Token** - [Get one here](#step-3-create-your-discord-bot)
3. **An AI Provider** - **Any** OpenAI-compatible API:
   - Cloud API (DeepSeek, OpenAI, Anthropic via OpenRouter)
   - Local LLM (llama.cpp, Ollama, LM Studio)

---

## Quick Start

### Step 1: Get the Code

```bash
git clone https://github.com/TheLonelyDevil9/discord-pals.git
cd discord-pals
```

### Step 2: Run Interactive Setup

**Windows:**

Double-click `setup.bat`

**Mac/Linux:**

```bash
chmod +x setup.sh
./setup.sh
```

The setup wizard will:

1. Create a Python virtual environment
2. Install all dependencies
3. Prompt you for AI providers (count, URLs, models)
4. Prompt you for Discord bots (single or multi-bot)
5. Generate `providers.json`, `bots.json`, and `.env`
6. Open `.env` for you to add API keys

### Step 3: Create Your Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **"New Application"** → Name it → Click **"Create"**
3. Go to **"Bot"** in the left sidebar
4. Click **"Reset Token"** → Copy the token (save it!)
5. Enable these **Privileged Intents**:
   - PRESENCE INTENT
   - SERVER MEMBERS INTENT
   - MESSAGE CONTENT INTENT
6. Go to **"OAuth2"** → **"URL Generator"**
7. Check: `bot` and `applications.commands`
8. Under Bot Permissions, check:
   - View Channels
   - Send Messages
   - Read Message History
   - Add Reactions
   - Use External Emojis
   - Manage Messages

9. Copy the generated URL and open it to invite your bot!

### Step 4: Configure AI Provider

See [AI Provider Setup](#-ai-provider-setup) below for detailed instructions.

### Step 5: Configure the environment

1. Copy the contents of `.env.example` to a new file called `.env`
2. Add your tokens. Any of the below can be set up in .env:

```
Used by providers.json - add keys for your configured providers.

OPENAI_API_KEY=your_openai_api_key
OPENROUTER_API_KEY=your_openrouter_api_key
DEEPSEEK_API_KEY=your_deepseek_api_key
LOCAL_API_KEY=optional
```

### Step 6: Run the Bot

```bash
python main.py
```

You should see:

```text
Loaded character: <Your Bot Here>
Synced __ commands (STC)
YourBot#1234 is online!
```

---

## AI Provider Setup

Discord Pals supports any OpenAI-compatible API. Choose your provider:

### Option A: DeepSeek (Recommended - Cheap & Good)

1. Go to [platform.deepseek.com](https://platform.deepseek.com/)
2. Create account → Add credits ($2-5 is plenty)
3. Go to API Keys → Create new key
4. Add to `.env`:

   ```env
   DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx
   ```

5. Create `providers.json`:

   ```json
   {
     "providers": [
       {
         "name": "DeepSeek",
         "url": "https://api.deepseek.com/v1",
         "key_env": "DEEPSEEK_API_KEY",
         "model": "deepseek-reasoner"
       }
     ],
     "timeout": 60
   }
   ```

### Option B: OpenAI

1. Go to [platform.openai.com](https://platform.openai.com/)
2. Create API key
3. Add to `.env`:

   ```env
   OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx
   ```

4. Create `providers.json`:

   ```json
   {
     "providers": [
       {
         "name": "OpenAI",
         "url": "https://api.openai.com/v1",
         "key_env": "OPENAI_API_KEY",
         "model": "gpt-5.2"
       }
     ],
     "timeout": 60
   }
   ```

### Option C: Local LLM (llama.cpp, Ollama, LM Studio, etc)

No API key needed! Just point to your local server:

```json
{
  "providers": [
    {
      "name": "Local LLM",
      "url": "http://localhost:8080/v1",
      "model": "local-model"
    }
  ],
  "timeout": 600
}
```

> **Tip:** For local LLMs, you can omit `key_env` entirely - it auto-detects that no key is needed.

Add placeholder to `.env`:

```env
LOCAL_API_KEY=optional
```

### Multiple Providers (Fallback Chain)

Set up multiple providers for redundancy:

```json
{
  "providers": [
    {
      "name": "Primary - Home Server",
      "url": "http://100.x.x.x:8080/v1",
      "key_env": "LOCAL_API_KEY",
      "model": "llama-3",
      "requires_key": false
    },
    {
      "name": "Fallback - DeepSeek",
      "url": "https://api.deepseek.com/v1",
      "key_env": "DEEPSEEK_API_KEY",
      "model": "deepseek-chat"
    }
  ],
  "timeout": 60
}
```

The bot tries each provider in order until one succeeds.

### Custom Provider Options (`extra_body`)

Some providers support extra request body parameters. Add `extra_body` to your provider config:

```json
{
  "providers": [
    {
      "name": "Custom Provider",
      "url": "https://api.example.com/v1",
      "key_env": "API_KEY",
      "model": "model-name",
      "extra_body": {
        "thinking": {"type": "disabled"},
        "top_k": 20,
        "repetition_penalty": 1.1
      }
    }
  ],
  "timeout": 60
}
```

The `extra_body` object is merged into the API request, useful for provider-specific parameters.

### Diagnosing Provider Issues

Run the built-in diagnostics script:

```bash
python diagnose.py
```

This checks:

- Configuration files exist
- API keys are set
- Provider connectivity
- Model availability

---

## 🧠 Memory Architecture

The bot uses a layered memory system for intelligent context management:

| Memory Type | Scope | File Location |
|-------------|-------|---------------|
| **Global User Profile** | Cross-server (follows users everywhere) | `bot_data/user_profiles.json` |
| **DM Memories** | Per-character (each character has own DM memories) | `bot_data/dm_memories/{character}.json` |
| **User Memories** | Per-character, per-server | `bot_data/user_memories/{character}.json` |
| **Server Memories** | Shared across all characters | `bot_data/memories.json` |
| **Lore** | Shared across all characters | `bot_data/lore.json` |

When a user talks to the bot, context is loaded in priority order:

1. Global user profile (facts that follow them across servers)
2. Character-specific memories about that user
3. Server-wide memories

---

## 💬 Commands

### Core Commands

| Command                  | Description                               |
| ------------------------ | ----------------------------------------- |
| `/status`                | Check bot and provider status             |
| `/reload`                | Reload character file (hot-reload)        |
| `/clear`                 | Clear conversation history                |
| `/recall <count>`        | Load last N messages (1-200)              |

### Character Commands

| Command                  | Description                               |
| ------------------------ | ----------------------------------------- |
| `/character list`        | List available characters                 |
| `/character set <name>`  | Switch to a different character           |
| `/character reload`      | Hot-reloads current character file        |

### Memory Commands

| Command                  | Description                               |
| ------------------------ | ----------------------------------------- |
| `/memory <text>`         | Save a memory                             |
| `/memories`              | View saved memories                       |
| `/lore <text>`           | Add server lore                           |

### Moderation Commands

| Command                  | Description                               |
| ------------------------ | ----------------------------------------- |
| `/autonomous <on/off>`   | Toggle random responses (5% default)      |
| `/stop [enable]`         | Pause/resume bot-to-bot interactions (flag optional) |
| `/delete_messages <N>`   | Delete bot's last N messages              |

---

## 🤖 Autonomous Mode

Autonomous mode allows bots to randomly join conversations without being explicitly mentioned. This feature is highly configurable per-channel via the web dashboard.

### How It Works

1. **Per-Channel Configuration**: Enable/disable autonomous mode for specific channels
2. **Response Chance**: Set the probability (1-50%) that the bot will respond to any message
3. **Cooldown**: Set minimum time between autonomous responses (1-10 minutes)
4. **Bot Triggers**: Control whether other bots/apps can trigger autonomous responses

### Name Triggers

Name triggers (responding when the bot's name/nickname is mentioned without @) are now a **subset of autonomous mode**:

- Name triggers only work in channels where autonomous mode is enabled
- The `name_trigger_chance` runtime config controls the probability of responding to name mentions
- If `allow_bot_triggers` is disabled for a channel, bots cannot trigger name-based responses

### Dashboard Configuration

Access the Channels page in the web dashboard to configure:

1. Click **⚙️ Configure** on any channel
2. Toggle **Enable Autonomous Responses**
3. Adjust **Response Chance** slider (1-50%)
4. Adjust **Cooldown** slider (1-10 minutes)
5. Toggle **🤖 Allow Bot Triggers** to control whether other bots can trigger responses

Channels with bot triggers enabled show a 🤖 icon in the status column.

### Quick Toggle

Click the ON/OFF badge in the Autonomous column to quickly enable/disable autonomous mode while preserving other settings.

### Fun Commands (17 total!)

| Command        | Description                    |
| -------------- | ------------------------------ |
| `/affection`   | Check affection level          |
| `/kiss`        | Kiss the bot                   |
| `/hug`         | Hug the bot                    |
| `/bonk`        | Bonk the bot                   |
| `/bite`        | Bite the bot                   |
| `/pat`         | Pat the bot's head             |
| `/poke`        | Poke the bot                   |
| `/tickle`      | Tickle the bot                 |
| `/slap`        | Slap the bot                   |
| `/cuddle`      | Cuddle with the bot            |
| `/holdhands`   | Hold hands with the bot        |
| `/squish`      | Squish the bot's face          |
| `/spank`       | Spank the bot                  |
| `/joke`        | Get a joke                     |
| `/compliment`  | Get a compliment               |
| `/roast`       | Get roasted (playfully)        |
| `/fortune`     | Get your fortune told          |
| `/challenge`   | Challenge the bot              |

---

## Creating Characters

### Basic Character

1. Go to `characters/` folder
2. Create `mycharacter.md`:

```markdown
# Character Name

## Persona

Write your character's personality, backstory, appearance, mannerisms here.
Be as detailed as you want - the AI will use all of it!

## Special Users

### YourDiscordName (this has to be the full username such as thelonelydevil)
How to treat this specific user differently.

### DiscordName2
Stuff/special treatment, etc.
```

1. Run `/character set mycharacter`

### Advanced Character Template

```markdown
# Samuel

## Persona

`{{user}}`: Introduction?
`Samuel`: *smiles warmly* "Hey there! I'm Sam - short for Samuel. 
I'm a coffee addict, terrible at mornings, and I collect vintage vinyl records."

`{{user}}`: Personality?
`Samuel`: "Hmm, let's see... I'm pretty chill, maybe a bit sarcastic, 
definitely loyal to my friends. I hate small talk but I'll debate 
philosophy for hours."

```

Samuel's personality: warm, sarcastic, loyal, coffee-addict, night-owl;
Samuel's likes: vinyl records, black coffee, rainy days, deep conversations;
Samuel's dislikes: mornings, small talk, dishonesty;
Samuel's speech: casual, uses contractions, occasional swearing, dry humor;

```

## Special Users

### TheLonelyDevil

Samuel's best friend. Very comfortable around them, teases them often.

```

## Running Multiple Bots

Run multiple characters from ONE process:

### Step 1: Create `bots.json`

```json
{
  "bots": [
    {"name": "Firefly", "token_env": "FIREFLY_TOKEN", "character": "firefly"},
    {"name": "Nahida", "token_env": "NAHIDA_TOKEN", "character": "nahida"},
    {"name": "Samuel", "token_env": "SAM_TOKEN", "character": "samuel"}
  ]
}
```

### Step 2: Add the **Discord** tokens to `.env`

```env
FIREFLY_TOKEN=your_firefly_bot_token
NAHIDA_TOKEN=your_nahida_bot_token
SAM_TOKEN=your_sam_bot_token
```

### Step 3: Run

```bash
python main.py
```

Output:

```text
Starting 3 bot(s)...
[Firefly] Loaded character: Firefly
[Nahida] Loaded character: Nahida
[Samuel] Loaded character: Samuel
🤖 [Firefly] Fly#1234 is online!
🤖 [Nahida] Nahida#5678 is online!
🤖 [Samuel] Sam#9012 is online!
```

> **Note:** Each bot needs its own Discord Application ID, registration steps 3-1 through 3-9.

---

## File Structure

```text
discord-pals/
├── main.py              # Main bot code
├── config.py            # Settings
├── providers.py         # AI providers
├── character.py         # Character loader
├── memory.py            # Memory system
├── discord_utils.py     # Discord helpers
├── request_queue.py     # Anti-spam
├── startup.py           # Startup validation
├── diagnose.py          # Provider diagnostics
├── setup.bat / setup.sh # Interactive setup
├── run.bat / run.sh     # Start the bot
├── providers.json       # AI provider config
├── bots.json            # Multi-bot config **(Optional)**
├── .env                 # Your tokens **(DO NOT COMMIT)**
├── characters/          # Character definitions
│   ├── template.md      # Example template
│   └── your-character.md
├── prompts/             # System prompt templates
│   ├── system.md
│   └── response_rules.md
└── bot_data/            # Runtime data (memories, lore)
```

---

## Troubleshooting

### "DISCORD_TOKEN not set!"

→ Create `.env` file with your token (not `.env.example`)

### "No characters available!"

→ Add `.md` files to `characters/` folder

### Bot online but doesn't respond

→ Enable MESSAGE CONTENT INTENT in Discord Developer Portal

### Commands don't show up

→ Wait 1 hour or kick and re-invite the bot

### "All providers failed"

→ Run `python diagnose.py` to check connectivity

### Provider timeout

→ Increase timeout in `providers.json` (try 120+ for local LLMs)

---

## Tips

- **Hot-reload:** Edit character files, then `/reload`
- **Switch characters:** Use `/switch` to list or change characters without restart
- **Web dashboard:** Open <http://localhost:5000> when bot is running
- **Test in DMs:** DM the bot directly for quiet testing
- **Check status:** Use `/status` to verify provider health
- **Diagnose issues:** Run `python diagnose.py` for detailed checks

---

## Need Help?

1. **Check Python:** `python --version` (need 3.10+)
2. **Check packages:** `pip list | grep discord`
3. **Run diagnostics:** `python diagnose.py`
4. **Check logs:** Look for error messages in terminal

---

Made with ❤️ by TLD (and Opus 4.5).

Credits to SpicyMarinara again for the inspiration, and Geechan for naming the project as well as the system prompt!
