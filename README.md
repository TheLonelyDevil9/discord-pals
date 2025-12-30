<p align="center">
  <img src="images/banner.jpg" alt="Discord Pals" width="600">
</p>

# Discord Pals 🤖

Heavily inspired by SpicyMarinara's [Discord Buddy](https://github.com/SpicyMarinara/Discord-Buddy) repo. It was so easy to make work and tweak after.

This is a modified version of Discord Buddy called Discord Pals, which is a templatized Discord bot that can roleplay as any character loaded from simple markdown files.
Supports cloud AI providers (OpenAI-compatible APIs work, DeepSeek, etc.) or your own local LLM.

## ✨ Features

- 🎭 **Any character** - Load characters from markdown files
- 🔄 **Plug-and-play AI providers** - Configure via JSON, no code changes
- 🏠 **Local LLM support** - Use llama.cpp, Ollama, LM Studio, or any OpenAI-compatible API
- 🔁 **Provider fallback** - Auto-retry with backup providers if one fails
- 🩺 **Diagnose script** - Built-in connectivity checker for troubleshooting
- 🤖 **Multi-bot support** - Run multiple bots from one process
- 💾 **Memory system** - Bot remembers important moments
- 🎮 **17 fun commands** - `/kiss`, `/hug`, `/bonk`, `/cuddle`, `/roast`, and more
- 💬 **Smart responses** - Tracks who you're replying to with full message context
- 🛡️ **Anti-spam** - Request queue with rate limiting built-in
- 📜 **History recall** - Recover context after clearing with `/recall` (up to 200 messages)
- ✨ **Customizable prompts** - Edit prompt templates without touching code
- 🤖 **Autonomous mode** - Bot randomly joins conversations (configurable)

---

## 📋 Requirements

1. **Python 3.10+** - [Download here](https://www.python.org/downloads/)
2. **A Discord Bot Token** - [Get one here](#step-3-create-your-discord-bot)
3. **An AI Provider** - Any OpenAI-compatible API:
   - ☁️ Cloud API (DeepSeek, OpenAI, Anthropic via OpenRouter)
   - 🏠 Local LLM (llama.cpp, Ollama, LM Studio)

---

## 🚀 Quick Start

### Step 1: Get the Code

```bash
git clone https://github.com/YOUR_USERNAME/discord-pals.git
cd discord-pals
```

### Step 2: Run Interactive Setup

**Windows:** Double-click `setup.bat`

**Mac/Linux:**

```bash
chmod +x setup.sh
./setup.sh
```

The setup wizard will:

1. ✅ Create a Python virtual environment
2. ✅ Install all dependencies
3. ✅ Prompt you for AI providers (count, URLs, models)
4. ✅ Prompt you for Discord bots (single or multi-bot)
5. ✅ Generate `providers.json`, `bots.json`, and `.env`
6. ✅ Open `.env` for you to add API keys

### Step 3: Create Your Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **"New Application"** → Name it → Click **"Create"**
3. Go to **"Bot"** in the left sidebar
4. Click **"Reset Token"** → Copy the token (save it!)
5. Enable these **Privileged Intents**:
   - ✅ MESSAGE CONTENT INTENT
   - ✅ SERVER MEMBERS INTENT
6. Go to **"OAuth2"** → **"URL Generator"**
7. Check: `bot` and `applications.commands`
8. Under Bot Permissions, check:
   - ✅ View Channels
   - ✅ Send Messages
   - ✅ Read Message History
   - ✅ Add Reactions
   - ✅ Use External Emojis
   - ✅ Manage Messages
   - ✅ Embed Links
   - ✅ Attach Files
9. Copy the generated URL and open it to invite your bot!

### Step 4: Configure AI Provider

See [AI Provider Setup](#-ai-provider-setup) below for detailed instructions.

### Step 5: Configure Environment

1. Copy `.env.example` to `.env`
2. Add your tokens:

```env
DISCORD_TOKEN=your_discord_bot_token_here
DEEPSEEK_API_KEY=your_api_key_here
```

### Step 6: Run the Bot

```bash
python main.py
```

You should see:

```text
✅ Loaded character: Firefly
✅ Synced 26 commands
🤖 YourBot#1234 is online!
```

---

## 🧠 AI Provider Setup

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
         "model": "deepseek-chat"
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
         "model": "gpt-4o-mini"
       }
     ],
     "timeout": 60
   }
   ```

### Option C: Local LLM (llama.cpp, Ollama, LM Studio)

No API key needed! Just point to your local server:

```json
{
  "providers": [
    {
      "name": "Local LLM",
      "url": "http://localhost:8080/v1",
      "key_env": "LOCAL_API_KEY",
      "model": "local-model",
      "requires_key": false
    }
  ],
  "timeout": 120
}
```

Add placeholder to `.env`:

```env
LOCAL_API_KEY=not-needed
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
| `/character reload`      | Reload current character file             |

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
| `/delete_messages <N>`   | Delete bot's last N messages              |

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

## 🎭 Creating Characters

### Basic Character

1. Go to `characters/` folder
2. Create `mycharacter.md`:

```markdown
# Character Name

## Persona

Write your character's personality, backstory, appearance, mannerisms here.
Be as detailed as you want - the AI will use all of it!

## Special Users

### YourDiscordName
How to treat this specific user differently.
```

1. Run `/character set mycharacter`

### Advanced Character Template

```markdown
# Samantha

## Persona

`{{user}}`: Introduction?
`Samantha`: *smiles warmly* "Hey there! I'm Sam - short for Samantha. 
I'm a coffee addict, terrible at mornings, and I collect vintage vinyl records."

`{{user}}`: Personality?
`Samantha`: "Hmm, let's see... I'm pretty chill, maybe a bit sarcastic, 
definitely loyal to my friends. I hate small talk but I'll debate 
philosophy for hours."

```yaml
Samantha's personality: warm, sarcastic, loyal, coffee-addict, night-owl
Samantha's likes: vinyl records, black coffee, rainy days, deep conversations
Samantha's dislikes: mornings, small talk, dishonesty
Samantha's speech: casual, uses contractions, occasional swearing, dry humor
```

## Special Users

### TheLonelyDevil

Samantha's best friend. Very comfortable around them, teases them often.

```

---

## ☁️ Cloud Deployment (Oracle Cloud)

### Step 1: Create Instance

1. Oracle Cloud → Compute → Create Instance
2. Choose **Ubuntu 22.04**
3. Shape: **VM.Standard.E2.1.Micro** (Always Free)
4. Add your SSH key
5. Create!

### Step 2: SSH In

```bash
ssh ubuntu@<PUBLIC_IP>
```

### Step 3: Install Dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3 python3-pip python3-venv git -y
```

### Step 4: Clone & Setup

```bash
git clone https://github.com/YOUR_USERNAME/discord-pals.git
cd discord-pals
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 5: Configure

```bash
cp .env.example .env
nano .env  # Add your tokens

cp providers.json.example providers.json
nano providers.json  # Configure your AI provider
```

### Step 6: Install Tailscale (if using remote LLM)

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

### Step 7: Test Run

```bash
python main.py
```

### Step 8: Run Forever (systemd)

```bash
sudo nano /etc/systemd/system/discord-pals.service
```

Paste:

```ini
[Unit]
Description=Discord Pals Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/discord-pals
ExecStart=/home/ubuntu/discord-pals/venv/bin/python main.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable discord-pals
sudo systemctl start discord-pals
```

### Useful Commands

| Command | Description |
|---------|-------------|
| `sudo systemctl status discord-pals` | Check status |
| `sudo systemctl restart discord-pals` | Restart bot |
| `sudo systemctl stop discord-pals` | Stop bot |
| `journalctl -u discord-pals -f` | View live logs |
| `journalctl -u discord-pals -n 100` | View last 100 log lines |

---

## 🤖 Running Multiple Bots

Run multiple characters from ONE process:

### Step 1: Create `bots.json`

```json
{
  "bots": [
    {"name": "Firefly", "token_env": "FIREFLY_TOKEN", "character": "firefly"},
    {"name": "Nahida", "token_env": "NAHIDA_TOKEN", "character": "nahida"},
    {"name": "Samantha", "token_env": "SAM_TOKEN", "character": "samantha"}
  ]
}
```

### Step 2: Add tokens to `.env`

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
🚀 Starting 3 bot(s)...
✅ [Firefly] Loaded character: Firefly
✅ [Nahida] Loaded character: Nahida
✅ [Samantha] Loaded character: Samantha
🤖 [Firefly] Fly#1234 is online!
🤖 [Nahida] Nahida#5678 is online!
🤖 [Samantha] Sam#9012 is online!
```

> **Note:** Each bot needs its own Discord Application.

---

## 📁 File Structure

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
├── bots.json            # Multi-bot config (optional)
├── .env                 # Your tokens (DO NOT COMMIT)
├── characters/          # Character definitions
│   ├── template.md      # Example template
│   └── your-character.md
├── prompts/             # System prompt templates
│   ├── system.md
│   └── response_rules.md
└── bot_data/            # Runtime data (memories, lore)
```

---

## 🔧 Troubleshooting

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

→ Increase timeout in `providers.json` (try 120 for local LLMs)

### Tailscale not connecting

→ Make sure both devices are logged into the same Tailscale account

---

## 💡 Tips

- **Hot-reload:** Edit character files, then `/character reload`
- **Test in DMs:** DM the bot directly for quiet testing
- **Check status:** Use `/status` to verify provider health
- **Diagnose issues:** Run `python diagnose.py` for detailed checks
- **View logs:** `journalctl -u discord-pals -f` (on Linux)

---

## ❓ Need Help?

1. **Check Python:** `python --version` (need 3.10+)
2. **Check packages:** `pip list | grep discord`
3. **Run diagnostics:** `python diagnose.py`
4. **Check logs:** Look for error messages in terminal

---

## 📄 License

MIT License - do whatever you want with it!

---

Made with ❤️ by TLD (and AI assistants)
