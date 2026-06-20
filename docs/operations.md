# Operations

This page covers local operations, deployment, update recovery, file layout, and troubleshooting.

## Dashboard Security

The dashboard starts automatically with `python main.py` and listens on `0.0.0.0:5000`. That can be reachable from other devices if the firewall or host allows it.

Set a password before exposing the dashboard outside a trusted machine or private network:

```env
DASHBOARD_USER=admin
DASHBOARD_PASS=your_secure_password
```

When `DASHBOARD_PASS` is set, dashboard pages require login and sessions persist until logout or browser close. When it is unset, authentication is disabled.

Dashboard write routes use CSRF tokens. If a custom script calls dashboard APIs, send the token with the form field `csrf_token` or header `X-CSRF-Token`.

## Updates And Recovery

The dashboard updater is the normal update path. It checks GitHub releases and remote tags, backs up local state, installs dependencies, and requests a restart when new code is staged. Its branch selector defaults to **Current**, which preserves the historical current checkout/upstream and release-tag behavior for existing installs. Choosing **Main** or **Staging** explicitly updates from that remote branch and skips the release-tag override for that run.

If the dashboard updater is too old or reports that it is current while a newer badge is visible, run:

```bash
python update.py
```

If `update.py` is missing in an old install, download the latest `update.py` from the repository into the Discord Pals folder and run it with Python.

Before Git mutations, the updater backs up local state under `bot_data/update_backups/pre-update-<timestamp>/`. Backups include bot/provider config, runtime data, characters, and local prompt files. Update outcomes are recorded in `bot_data/update_log.json` without tokens or message contents.

Release tags should be cut only after the release commit is on `main` or an approved release branch. The `bump_version.py --tag` flow updates the version, writes changelog content, creates the tag, and publishes `main` plus the tag.

## Deployment

### Windows Task Scheduler

1. Open Task Scheduler with `taskschd.msc`.
2. Create a basic task named `Discord Pals Bot`.
3. Trigger it when the computer starts.
4. Start `C:\path\to\discord-pals\run.bat`.
5. In task properties, enable running whether the user is logged on or not.

To hide the console, use a small VBS wrapper:

```vbs
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd /c cd /d C:\path\to\discord-pals && venv\Scripts\python.exe main.py", 0
Set WshShell = Nothing
```

### Linux systemd

Create `/etc/systemd/system/discord-pals.service`:

```ini
[Unit]
Description=Discord Pals Bot
After=network.target

[Service]
Type=simple
User=your-username
WorkingDirectory=/opt/discord-pals
ExecStart=/opt/discord-pals/venv/bin/python main.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Enable and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable discord-pals
sudo systemctl start discord-pals
sudo systemctl status discord-pals
sudo journalctl -u discord-pals -f
```

### Docker

Minimal `Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN python startup.py --init-configs

CMD ["python", "main.py"]
```

Minimal `docker-compose.yml`:

```yaml
version: "3.8"
services:
  discord-pals:
    build: .
    restart: unless-stopped
    environment:
      DISCORD_TOKEN: ${DISCORD_TOKEN}
      OPENAI_API_KEY: ${OPENAI_API_KEY}
    volumes:
      - ./bot_data:/app/bot_data
      - ./characters:/app/characters
      - ./prompts:/app/prompts
    ports:
      - "5000:5000"
```

`python startup.py --init-configs` creates starter `.env` and `providers.json` files without prompts and without writing secrets. Runtime environment variables from Docker, Compose, systemd, or a host shell can satisfy startup validation, so mounting `.env` is optional. Mount `providers.json` or `bots.json` only when you want to manage those files outside the container image.

For multi-bot Docker runs, mount or bake a `bots.json` file and add one environment variable per bot `token_env`:

```yaml
environment:
  FIREFLY_DISCORD_TOKEN: ${FIREFLY_DISCORD_TOKEN}
  NAHIDA_DISCORD_TOKEN: ${NAHIDA_DISCORD_TOKEN}
volumes:
  - ./bots.json:/app/bots.json:ro
```

Run:

```bash
docker-compose up -d
docker-compose logs -f
```

Remove the dashboard port mapping if you do not need remote dashboard access.

### VPS Notes

Recommended baseline:

- 1 GB RAM minimum; 2 GB or more for multiple bots.
- 1 vCPU.
- 10 GB storage.

For private dashboard access, prefer firewall rules, SSH tunneling, or a reverse proxy with authentication.

SSH tunnel example:

```bash
ssh -L 5000:localhost:5000 user@your-server
```

Nginx reverse proxy sketch:

```nginx
server {
    listen 443 ssl;
    server_name dashboard.yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    auth_basic "Discord Pals Dashboard";
    auth_basic_user_file /etc/nginx/.htpasswd;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Create the password file with:

```bash
sudo htpasswd -c /etc/nginx/.htpasswd admin
```

## File Layout

```text
discord-pals/
|-- main.py                  # Entry point and multi-bot launcher
|-- bot_instance.py          # Discord event orchestration and response lifecycle
|-- coordinator.py           # Multi-bot request coordination
|-- config.py                # Provider and startup config loading
|-- runtime_config.py        # Live-adjustable runtime settings
|-- providers.py             # AI provider abstraction and fallback chain
|-- character.py             # Character Markdown parsing
|-- memory.py                # Learned memory and manual lore stores
|-- reminders.py             # Reminder scheduling and delivery state
|-- time_utils.py            # Timezones and prompt time context
|-- scopes.py                # Shared scope identifiers
|-- discord_utils.py         # Discord helpers, history, topology, and JSON helpers
|-- response_sanitizer.py    # Output cleanup and identity guard helpers
|-- request_queue.py         # Request queue and rate limiting
|-- user_ignores.py          # User ignore system
|-- security.py              # Dashboard auth and CSRF
|-- logger.py                # Logging
|-- stats.py                 # Message statistics
|-- prometheus_metrics.py    # Metrics integration
|-- startup.py               # Startup validation
|-- version.py               # Version constant
|-- diagnose.py              # Provider diagnostics
|-- bump_version.py          # Release helper
|-- update.py                # Bootstrap updater
|-- commands/                # Slash command modules
|-- characters/              # Character definitions
|-- prompts/                 # Prompt text
|-- templates/               # Dashboard HTML
|-- images/                  # Dashboard/readme images
`-- bot_data/                # Runtime state
```

## Troubleshooting

### Token errors

Single-bot mode needs `DISCORD_TOKEN` from `.env` or the process environment. The dashboard Config page Advanced tab can update `.env`; saved tokens are never displayed and require a bot restart before they take effect.

Multi-bot mode uses the token variable names listed in `bots.json`. The Config page Advanced tab shows one token field per declared `token_env`, writes the matching `.env` variable, and never stores literal tokens in `bots.json`. In container deployments where secrets come from Compose or the host environment, update those runtime variables outside the dashboard.

### No characters available

Add `.md` files to `characters/`. The filename is the switch name; the first heading is the display name.

### Bot online but not responding

Check:

- Message Content Intent in the Discord Developer Portal.
- `server_responses_enabled` or `dm_responses_enabled`.
- Channel whitelist/blacklist and DM user blacklist.
- Whether the user has ignored that bot.
- Whether global pause or bot interaction pause is active.

### Slash commands missing

Restart the bot so commands sync again. Confirm the invite URL included `applications.commands`. Grouped commands appear as nested commands, such as `/timezone set` and `/reminders list`.

### All providers failed

Run:

```bash
python diagnose.py
```

Then check API keys, provider URLs, model names, and network access. For slow local models, raise provider `timeout`.

### Character edits not active

Use `/reload` or the dashboard reload action. Character files are cached until reloaded.

### Dashboard not accessible

Open <http://localhost:5000> on the host running the bot. For remote access, check firewall rules, port forwarding, reverse-proxy config, and dashboard password settings.

### Memories not saving

Check that `bot_data/` exists and is writable. Use the dashboard or memory commands rather than editing JSON while the bot is running.

### Bot loops or talks too much

Use `/stop` for bot-to-bot conversations, disable bot triggers on the channel, disable autonomous mode, lower `name_trigger_chance`, or turn off nickname triggers.

### Rate limits

The bot retries 429s with backoff. Persistent rate limits usually mean the provider quota is too low for current traffic. Add a fallback provider or reduce usage.

### Context seems wrong

Use `/history clear` to reset a chat. Use `/recall` to pull recent Discord messages back into context. Review `history_limit`, `immediate_message_count`, identity guard, and bot reply reference settings.

### Startup crash

Common causes:

- Invalid JSON in `providers.json`, `bots.json`, or runtime config.
- Missing environment variables.
- Python older than 3.10.
- Missing dependencies.

Run `pip install -r requirements.txt`, then `python diagnose.py`.
