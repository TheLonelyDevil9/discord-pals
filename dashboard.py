"""
Discord Pals - Web Dashboard
Local web interface for managing bot, memories, and characters.
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file, session
import threading
import json
import os
import io
import time
import zipfile
from pathlib import Path
from datetime import timedelta
import logger as log
from security import (
    safe_path, safe_filename, validate_zip_entry,
    get_or_create_secret_key, requires_auth, requires_csrf,
    generate_csrf_token, requires_login, check_dashboard_auth,
    login_user, logout_user, is_auth_enabled
)
from constants import ALLOWED_IMPORT_FILES
from config import PROVIDERS, CHARACTER_PROVIDERS
from version import VERSION

app = Flask(__name__, template_folder='templates', static_folder='images', static_url_path='/static')

# Shared state (set by main.py)
bot_instances = []
DATA_DIR = Path("bot_data")
CHARACTERS_DIR = Path("characters")
PROMPTS_DIR = Path("prompts")

# Initialize secret key securely
DATA_DIR.mkdir(parents=True, exist_ok=True)
app.secret_key = get_or_create_secret_key(DATA_DIR)

# Flask session cookie security settings
app.config.update(
    # Only send cookies over HTTPS (set to False for local development)
    SESSION_COOKIE_SECURE=False,
    # Prevent JavaScript access to cookies
    SESSION_COOKIE_HTTPONLY=True,
    # Restrict cookie to same-site requests
    SESSION_COOKIE_SAMESITE='Lax',
    # Set cookie expiration (24 hours)
    PERMANENT_SESSION_LIFETIME=timedelta(hours=24)
)

# Make CSRF token available in all templates
app.jinja_env.globals['csrf_token'] = generate_csrf_token


# Make auth status available in all templates
@app.context_processor
def inject_globals():
    """Inject global variables into all templates."""
    return {
        'auth_enabled': is_auth_enabled(),
        'is_logged_in': is_auth_enabled() and session.get('logged_in', False),
        'version': VERSION
    }


def _format_activity_time(timestamp: float | None) -> str:
    """Format a timestamp as a human-readable 'X ago' string."""
    if not timestamp:
        return "Never"
    elapsed = time.time() - timestamp
    if elapsed < 60:
        return f"{int(elapsed)}s ago"
    elif elapsed < 3600:
        return f"{int(elapsed // 60)}m ago"
    return f"{int(elapsed // 3600)}h ago"


# Global authentication check - protects all routes except login/logout/static
@app.before_request
def check_login():
    """Check authentication for all routes (if enabled)."""
    # Skip auth check for login/logout routes and static files
    if request.endpoint in ('login', 'logout', 'static'):
        return None
    if request.path.startswith('/static/'):
        return None

    # Validate content-type for JSON POST requests
    if request.method == 'POST' and request.is_json:
        if not request.content_type or 'application/json' not in request.content_type:
            log.warn(f"Invalid content-type for JSON request: {request.content_type}")
            return jsonify({'status': 'error', 'message': 'Invalid content-type'}), 400

    # If auth is enabled and not logged in, redirect to login
    if is_auth_enabled():
        from security import is_logged_in
        if not is_logged_in():
            return redirect(url_for('login', next=request.path))


def get_memory_files():
    """Get all memory JSON files."""
    # Files that are NOT memories and should be excluded
    excluded = {"autonomous", "runtime_config", "stats", "history"}
    files = {}
    if DATA_DIR.exists():
        for f in DATA_DIR.glob("*.json"):
            if f.stem not in excluded:
                files[f.stem] = f
    return files


def get_character_files():
    """Get all character markdown files."""
    files = []
    if CHARACTERS_DIR.exists():
        for f in CHARACTERS_DIR.glob("*.md"):
            if f.name != "template.md":
                files.append(f.name.replace(".md", ""))
    return files


# --- Login/Logout Routes ---

@app.route('/login', methods=['GET', 'POST'])
@requires_csrf
def login():
    """Login page and authentication handler."""
    error = None

    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')

        if check_dashboard_auth(username, password):
            login_user()
            next_page = request.args.get('next', '/')
            return redirect(next_page)
        else:
            error = "Invalid username or password"

    # If already logged in, redirect to dashboard
    if not is_auth_enabled():
        return redirect('/')

    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    """Log out and redirect to login page."""
    logout_user()
    return redirect('/login')


# --- Routes ---

@app.route('/')
def dashboard():
    """Main dashboard page."""
    import time
    import runtime_config
    
    last_activity = runtime_config.get_last_activity()
    bots_info = []
    for bot in bot_instances:
        bot_activity = last_activity.get(bot.name)
        activity_str = _format_activity_time(bot_activity)

        bots_info.append({
            'name': bot.name,
            'character': bot.character.name if bot.character else 'None',
            'online': bot.client.is_ready() if hasattr(bot, 'client') else False,
            'last_activity': activity_str
        })
    
    memory_count = len(get_memory_files())
    character_count = len(get_character_files())
    
    # Get autonomous channels count
    from discord_utils import autonomous_manager
    autonomous_count = len(autonomous_manager.enabled_channels)
    
    # Get global state for control panel
    global_paused = runtime_config.get("global_paused", False)
    bot_interactions_paused = runtime_config.get("bot_interactions_paused", False)
    use_single_user = runtime_config.get("use_single_user", True)
    
    return render_template('dashboard.html',
        bots=bots_info,
        memory_count=memory_count,
        character_count=character_count,
        autonomous_count=autonomous_count,
        global_paused=global_paused,
        bot_interactions_paused=bot_interactions_paused,
        use_single_user=use_single_user
    )


@app.route('/memories')
def memories():
    """Memories management page."""
    from stats import stats_manager
    from memory import memory_manager
    
    files = get_memory_files()
    memories_data = {}
    
    for name, path in files.items():
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                memories_data[name] = data
        except Exception as e:
            memories_data[name] = {"error": str(e)}
    
    user_names = stats_manager.get_all_user_names()
    
    # Get guilds for the dropdown
    guilds = []
    seen_ids = set()
    for bot in bot_instances:
        if hasattr(bot, 'client') and bot.client.is_ready():
            for guild in bot.client.guilds:
                if guild.id not in seen_ids:
                    seen_ids.add(guild.id)
                    # Get lore for this guild
                    lore = memory_manager.get_lore(guild.id)
                    guilds.append({
                        'id': guild.id,
                        'name': guild.name,
                        'lore': lore
                    })
    
    # Get available characters for memory assignment
    characters = get_character_files()
    
    return render_template('memories.html',
        memories=memories_data,
        user_names=user_names,
        guilds=guilds,
        characters=characters
    )


@app.route('/memories/<name>/delete', methods=['POST'])
@requires_csrf
def delete_memory(name):
    """Delete a specific memory entry."""
    try:
        file_path = safe_path(DATA_DIR, name, '.json')
    except ValueError as e:
        log.warn(f"Invalid memory path: {e}")
        return redirect(url_for('memories'))

    memory_key = request.form.get('key')

    if file_path.exists() and memory_key:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if memory_key in data:
                del data[memory_key]

                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2)
        except Exception as e:
            log.warn(f"Failed to delete memory '{memory_key}': {e}")

    return redirect(url_for('memories'))


@app.route('/memories/<name>/edit')
def edit_memory(name):
    """Edit a memory file."""
    try:
        file_path = safe_path(DATA_DIR, name, '.json')
    except ValueError as e:
        log.warn(f"Invalid memory path: {e}")
        return redirect(url_for('memories'))

    content = "{}"

    if file_path.exists():
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            log.warn(f"Failed to read memory file: {e}")

    return render_template('memory_edit.html', name=name, content=content)


@app.route('/memories/<name>/save', methods=['POST'])
@requires_csrf
def save_memory(name):
    """Save memory file changes."""
    try:
        file_path = safe_path(DATA_DIR, name, '.json')
    except ValueError as e:
        log.warn(f"Invalid memory path: {e}")
        return redirect(url_for('memories'))

    content = request.form.get('content', '{}')

    try:
        json.loads(content)  # Validate JSON
        DATA_DIR.mkdir(exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        log.warn(f"Failed to save memory: {e}")

    return redirect(url_for('memories'))


# --- Characters ---

@app.route('/characters')
def characters():
    """Characters viewer page (merged with Preview and Prompts)."""
    char_files = get_character_files()
    chars_data = {}
    
    for name in char_files:
        path = CHARACTERS_DIR / f"{name}.md"
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
                chars_data[name] = {
                    'preview': content[:500] + ('...' if len(content) > 500 else ''),
                    'full_path': str(path.absolute()),
                    'size': len(content)
                }
        except Exception as e:
            chars_data[name] = {'error': str(e)}
    
    # Load system prompt for Prompts tab
    system_content = ""
    system_path = PROMPTS_DIR / "system.md"
    if system_path.exists():
        try:
            with open(system_path, 'r', encoding='utf-8') as f:
                system_content = f.read()
        except Exception:
            pass
    
    return render_template('characters.html', 
                           characters=chars_data, 
                           character_list=char_files,
                           system_content=system_content)


@app.route('/characters/<name>/edit')
def edit_character(name):
    """Edit a character file."""
    try:
        path = safe_path(CHARACTERS_DIR, name, '.md')
    except ValueError as e:
        log.warn(f"Invalid character path: {e}")
        return redirect(url_for('characters'))

    content = ""

    if path.exists():
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            log.warn(f"Failed to read character file: {e}")

    return render_template('character_edit.html', name=name, content=content)


@app.route('/characters/<name>/save', methods=['POST'])
@requires_csrf
def save_character(name):
    """Save character file changes."""
    try:
        path = safe_path(CHARACTERS_DIR, name, '.md')
    except ValueError as e:
        log.warn(f"Invalid character path: {e}")
        return redirect(url_for('characters'))

    content = request.form.get('content', '')

    try:
        CHARACTERS_DIR.mkdir(exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        log.warn(f"Failed to save character: {e}")

    return redirect(url_for('characters'))


@app.route('/characters/<name>/delete', methods=['POST'])
@requires_csrf
def delete_character(name):
    """Delete a character file."""
    try:
        path = safe_path(CHARACTERS_DIR, name, '.md')
    except ValueError as e:
        log.warn(f"Invalid character path: {e}")
        return redirect(url_for('characters'))

    if path.exists():
        try:
            path.unlink()
        except Exception as e:
            log.warn(f"Failed to delete character: {e}")

    return redirect(url_for('characters'))


@app.route('/characters/new', methods=['POST'])
@requires_csrf
def new_character():
    """Create a new character file."""
    name = request.form.get('name', '').strip()

    if name:
        try:
            path = safe_path(CHARACTERS_DIR, name, '.md')
            if not path.exists():
                CHARACTERS_DIR.mkdir(exist_ok=True)
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(f"# {name}\n\n## Persona\n\nDescribe your character here.\n\n## Special Users\n\n")
            return redirect(url_for('edit_character', name=name))
        except ValueError as e:
            log.warn(f"Invalid character name: {e}")
        except Exception as e:
            log.warn(f"Failed to create character: {e}")

    return redirect(url_for('characters'))


# --- Settings ---

@app.route('/settings')
def settings():
    """Redirect to config page (settings merged into config)."""
    return redirect(url_for('config_page'))


@app.route('/settings/providers/save', methods=['POST'])
@requires_csrf
def save_providers():
    """Save providers.json."""
    content = request.form.get('content', '')
    try:
        json.loads(content)  # Validate JSON
        with open('providers.json', 'w') as f:
            f.write(content)
        return redirect(url_for('config_page', message='Providers saved successfully'))
    except json.JSONDecodeError as e:
        log.error(f"Failed to save providers.json: Invalid JSON - {e}")
        return redirect(url_for('config_page', error=f'Invalid JSON: {e}'))
    except Exception as e:
        log.error(f"Failed to save providers.json: {e}")
        return redirect(url_for('config_page', error=f'Save failed: {e}'))


@app.route('/api/providers/save', methods=['POST'])
@requires_csrf
def api_save_providers():
    """API endpoint to save providers.json (for AJAX calls)."""
    data = request.json or {}
    content = data.get('content', '')
    try:
        json.loads(content)  # Validate JSON
        with open('providers.json', 'w') as f:
            f.write(content)
        return jsonify({'status': 'ok'})
    except json.JSONDecodeError as e:
        return jsonify({'status': 'error', 'message': f'Invalid JSON: {e}'}), 400
    except Exception as e:
        log.error(f"Failed to save providers.json: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/settings/bots/save', methods=['POST'])
@requires_csrf
def save_bots():
    """Save bots.json."""
    content = request.form.get('content', '')
    try:
        json.loads(content)  # Validate JSON
        with open('bots.json', 'w') as f:
            f.write(content)
        return redirect(url_for('config_page', message='Bots config saved successfully'))
    except json.JSONDecodeError as e:
        log.error(f"Failed to save bots.json: Invalid JSON - {e}")
        return redirect(url_for('config_page', error=f'Invalid JSON: {e}'))
    except Exception as e:
        log.error(f"Failed to save bots.json: {e}")
        return redirect(url_for('config_page', error=f'Save failed: {e}'))


@app.route('/settings/autonomous/save', methods=['POST'])
@requires_csrf
def save_autonomous():
    """Save autonomous.json."""
    content = request.form.get('content', '')
    try:
        json.loads(content)  # Validate JSON
        DATA_DIR.mkdir(exist_ok=True)
        with open(DATA_DIR / 'autonomous.json', 'w') as f:
            f.write(content)
        return redirect(url_for('config_page', message='Autonomous config saved successfully'))
    except json.JSONDecodeError as e:
        log.error(f"Failed to save autonomous.json: Invalid JSON - {e}")
        return redirect(url_for('config_page', error=f'Invalid JSON: {e}'))
    except Exception as e:
        log.error(f"Failed to save autonomous.json: {e}")
        return redirect(url_for('config_page', error=f'Save failed: {e}'))


# --- Prompts ---

PROMPTS_DIR = Path("prompts")

@app.route('/prompts')
def prompts():
    """Redirect to characters page (prompts merged into characters)."""
    return redirect(url_for('characters'))


@app.route('/prompts/system/save', methods=['POST'])
@requires_csrf
def save_system_prompt():
    """Save system.md prompt."""
    content = request.form.get('content', '')
    try:
        PROMPTS_DIR.mkdir(exist_ok=True)
        with open(PROMPTS_DIR / 'system.md', 'w', encoding='utf-8') as f:
            f.write(content)
        # Reload prompts in character manager
        from character import character_manager
        character_manager.reload_prompts()
    except Exception as e:
        log.warn(f"Failed to save system.md: {e}")
    return redirect(url_for('characters'))


@app.route('/api/status')
def api_status():
    """API endpoint for bot status with extended info."""
    import time
    import runtime_config
    
    last_activity = runtime_config.get_last_activity()
    bots_info = []
    for bot in bot_instances:
        bot_activity = last_activity.get(bot.name)
        activity_str = _format_activity_time(bot_activity)

        bots_info.append({
            'name': bot.name,
            'character': bot.character.name if bot.character else None,
            'online': bot.client.is_ready() if hasattr(bot, 'client') else False,
            'last_activity': activity_str
        })
    
    # Include global state
    global_paused = runtime_config.get("global_paused", False)
    bot_interactions_paused = runtime_config.get("bot_interactions_paused", False)
    use_single_user = runtime_config.get("use_single_user", True)
    
    return jsonify({
        'bots': bots_info,
        'global_paused': global_paused,
        'bot_interactions_paused': bot_interactions_paused,
        'use_single_user': use_single_user
    })


@app.route('/api/killswitch', methods=['GET', 'POST'])
@requires_csrf
def api_killswitch():
    """API endpoint for global killswitch control."""
    import runtime_config
    
    if request.method == 'POST':
        data = request.json or {}
        new_state = data.get('enabled')
        
        # If no explicit state provided, toggle
        if new_state is None:
            new_state = not runtime_config.get("global_paused", False)
        
        runtime_config.set("global_paused", new_state)
        
        if new_state:
            log.warn("Global killswitch ACTIVATED via dashboard")
        else:
            log.ok("Global killswitch RELEASED via dashboard")
        
        return jsonify({
            'status': 'ok',
            'global_paused': new_state
        })
    
    # GET request
    return jsonify({
        'global_paused': runtime_config.get("global_paused", False)
    })


@app.route('/api/bot-interactions', methods=['GET', 'POST'])
@requires_csrf
def api_bot_interactions():
    """API endpoint for bot-to-bot interaction control."""
    import runtime_config
    
    if request.method == 'POST':
        data = request.json or {}
        new_state = data.get('paused')
        
        # If no explicit state provided, toggle
        if new_state is None:
            new_state = not runtime_config.get("bot_interactions_paused", False)
        
        runtime_config.set("bot_interactions_paused", new_state)
        
        if new_state:
            log.info("Bot-to-bot interactions PAUSED via dashboard")
        else:
            log.info("Bot-to-bot interactions RESUMED via dashboard")
        
        return jsonify({
            'status': 'ok',
            'bot_interactions_paused': new_state
        })
    
    # GET request
    return jsonify({
        'bot_interactions_paused': runtime_config.get("bot_interactions_paused", False)
    })


@app.route('/api/message-format', methods=['GET', 'POST'])
@requires_csrf
def api_message_format():
    """API endpoint for message format control (single-user vs multi-role)."""
    import runtime_config
    
    if request.method == 'POST':
        data = request.json or {}
        new_state = data.get('use_single_user')
        
        # If no explicit state provided, toggle
        if new_state is None:
            new_state = not runtime_config.get("use_single_user", True)
        
        runtime_config.set("use_single_user", new_state)
        
        if new_state:
            log.info("Message format set to SINGLE-USER (SillyTavern-style) via dashboard")
        else:
            log.info("Message format set to MULTI-ROLE (system/user/assistant) via dashboard")
        
        return jsonify({
            'status': 'ok',
            'use_single_user': new_state
        })
    
    # GET request
    return jsonify({
        'use_single_user': runtime_config.get("use_single_user", True)
    })


# --- Runtime Config ---

@app.route('/config')
def config_page():
    """Runtime configuration page (merged with Settings)."""
    import runtime_config
    
    config = runtime_config.get_all()
    characters = get_character_files()
    
    # Get providers
    providers = []
    providers_file = Path("providers.json")
    providers_raw = "{}"
    if providers_file.exists():
        try:
            with open(providers_file, 'r') as f:
                providers_raw = f.read()
            data = json.loads(providers_raw)
            providers = [p.get('name', f"Provider {i}") for i, p in enumerate(data.get('providers', []))]
        except Exception as e:
            log.warn(f"Failed to load providers for config page: {e}")
    
    # Load bots.json
    bots_raw = "{}"
    if os.path.exists('bots.json'):
        try:
            with open('bots.json', 'r') as f:
                bots_raw = f.read()
        except Exception:
            pass
    
    # Load autonomous.json
    autonomous_raw = "{}"
    autonomous_file = DATA_DIR / 'autonomous.json'
    if autonomous_file.exists():
        try:
            with open(autonomous_file, 'r') as f:
                autonomous_raw = f.read()
        except Exception:
            pass
    
    # Get bots and their current characters + autonomous channels
    from discord_utils import autonomous_manager
    bots_info = []
    for bot in bot_instances:
        # Get channel names for autonomous channels this bot can see
        auto_channels = []
        if hasattr(bot, 'client') and bot.client.is_ready():
            for channel_id in autonomous_manager.enabled_channels:
                channel = bot.client.get_channel(channel_id)
                if channel:
                    guild_name = channel.guild.name if hasattr(channel, 'guild') else 'DM'
                    auto_channels.append({
                        'id': channel_id,
                        'name': channel.name,
                        'guild': guild_name
                    })
        
        bots_info.append({
            'name': bot.name,
            'character': bot.character.name if bot.character else 'None',
            'online': bot.client.is_ready() if hasattr(bot, 'client') else False,
            'auto_channels': auto_channels,
            'nicknames': getattr(bot, 'nicknames', '')  # Per-bot custom nicknames
        })
    
    return render_template('config.html',
        config=config,
        characters=characters,
        providers=providers,
        provider_tiers=list(PROVIDERS.keys()),
        providers_dict=PROVIDERS,
        character_providers=CHARACTER_PROVIDERS,
        bots=bots_info,
        providers_raw=providers_raw,
        bots_raw=bots_raw,
        autonomous_raw=autonomous_raw,
        message=request.args.get('message'),
        error=request.args.get('error')
    )


@app.route('/api/config', methods=['GET', 'POST'])
@requires_csrf
def api_config():
    """API for runtime config."""
    import runtime_config

    if request.method == 'POST':
        data = request.json
        # Validate keys against allowed config keys
        allowed_keys = set(runtime_config.DEFAULTS.keys())
        for key, value in data.items():
            if key in allowed_keys:
                runtime_config.set(key, value)
            else:
                log.warn(f"Rejected unknown config key: {key}")
        return jsonify({'status': 'ok'})

    return jsonify(runtime_config.get_all())


@app.route('/api/switch_character', methods=['POST'])
@requires_csrf
def api_switch_character():
    """Switch character for a bot."""
    data = request.json
    bot_name = data.get('bot_name')
    character_name = data.get('character')
    
    for bot in bot_instances:
        if bot.name == bot_name:
            try:
                from character import character_manager
                bot.character = character_manager.load(character_name)
                return jsonify({'status': 'ok', 'character': bot.character.name})
            except Exception as e:
                return jsonify({'status': 'error', 'message': str(e)}), 400
    
    return jsonify({'status': 'error', 'message': 'Bot not found'}), 404


@app.route('/api/character-provider', methods=['GET', 'POST'])
@requires_csrf
def api_character_provider():
    """Get or set character provider preferences."""
    from config import reload_character_providers

    providers_file = Path("providers.json")

    if request.method == 'GET':
        return jsonify({'character_providers': CHARACTER_PROVIDERS})

    # POST request
    data = request.json or {}
    character = data.get('character', '')
    tier = data.get('tier', '')

    if not character:
        return jsonify({'status': 'error', 'message': 'Character name required'}), 400

    # Validate tier
    if tier and tier not in PROVIDERS:
        return jsonify({'status': 'error', 'message': f'Invalid tier: {tier}'}), 400

    try:
        # Load current providers.json
        if providers_file.exists():
            with open(providers_file, 'r') as f:
                providers_data = json.load(f)
        else:
            providers_data = {"providers": [], "timeout": 60}

        # Initialize character_providers if not exists
        if 'character_providers' not in providers_data:
            providers_data['character_providers'] = {}

        # Update or remove the character's provider preference
        if tier:
            providers_data['character_providers'][character] = tier
        else:
            # Empty tier means remove preference (use default)
            providers_data['character_providers'].pop(character, None)

        # Save back to file
        with open(providers_file, 'w') as f:
            json.dump(providers_data, f, indent=2)

        # Reload config
        reload_character_providers()

        log.info(f"Character provider preference updated: {character} -> {tier or 'default'}")

        return jsonify({
            'status': 'ok',
            'character': character,
            'tier': tier or 'default'
        })

    except Exception as e:
        log.error(f"Failed to save character provider: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/nicknames', methods=['GET', 'POST'])
@requires_csrf
def api_nicknames():
    """Get or update nicknames for bots. Persists to bots.json or runtime_config.json."""
    import runtime_config
    
    if request.method == 'GET':
        # Return all bot nicknames
        nicknames_data = {}
        for bot in bot_instances:
            nicknames_data[bot.name] = getattr(bot, 'nicknames', '')
        return jsonify({'nicknames': nicknames_data})
    
    # POST request
    data = request.json or {}
    bot_name = data.get('bot_name')
    nicknames = data.get('nicknames', '')
    
    for bot in bot_instances:
        if bot.name == bot_name:
            bot.nicknames = nicknames
            
            # Persist to bots.json if it exists (multi-bot mode)
            bots_file = Path('bots.json')
            persisted = False
            
            if bots_file.exists():
                try:
                    with open(bots_file, 'r') as f:
                        bots_config = json.load(f)
                    
                    # Find and update the bot's nicknames in config
                    for bot_cfg in bots_config.get('bots', []):
                        if bot_cfg.get('name') == bot_name:
                            bot_cfg['nicknames'] = nicknames
                            persisted = True
                            break
                    
                    if persisted:
                        with open(bots_file, 'w') as f:
                            json.dump(bots_config, f, indent=2)
                        log.info(f"Updated and persisted nicknames for {bot_name} to bots.json: {nicknames or '(none)'}")
                except Exception as e:
                    log.warn(f"Failed to persist nicknames to bots.json: {e}")
            
            # For single-bot mode or as fallback, also store in runtime_config
            if not persisted:
                # Store in runtime_config for single-bot mode persistence
                bot_nicknames = runtime_config.get('bot_nicknames', {})
                bot_nicknames[bot_name] = nicknames
                runtime_config.set('bot_nicknames', bot_nicknames)
                log.info(f"Updated and persisted nicknames for {bot_name} to runtime_config: {nicknames or '(none)'}")
            
            return jsonify({'status': 'ok', 'nicknames': nicknames, 'bot_name': bot_name})
    
    return jsonify({'status': 'error', 'message': 'Bot not found'}), 404


@app.route('/context')
def context_page():
    """Context visualization page."""
    import runtime_config
    
    contexts = runtime_config.get_last_context()
    
    return render_template('context.html', contexts=contexts, bots=bot_instances)


@app.route('/api/context/<bot_name>')
def api_context(bot_name):
    """Get last context for a specific bot."""
    import runtime_config

    context = runtime_config.get_last_context(bot_name)
    if context:
        return jsonify(context)
    return jsonify({'status': 'no context stored'})


@app.route('/api/contexts')
def api_contexts():
    """Get all bot contexts for live updates."""
    import runtime_config
    return jsonify(runtime_config.get_last_context())


# --- Stats ---

@app.route('/stats')
def stats_page():
    """Message statistics page."""
    from stats import stats_manager
    
    stats = stats_manager.get_summary()
    return render_template('stats.html', stats=stats)


# --- Character Preview ---

@app.route('/preview')
def preview_page():
    """Character preview page."""
    from character import character_manager
    
    characters = character_manager.list_available()
    return render_template('preview.html', characters=characters)


@app.route('/api/preview/<name>')
def api_preview(name):
    """Generate preview for a character."""
    from character import character_manager
    
    try:
        character = character_manager.load(name)
        if not character:
            return jsonify({'error': f'Character "{name}" not found'})
        
        # Build system prompt (character section only)
        system_prompt = character_manager.build_system_prompt(
            character=character,
            user_name="ExampleUser"
        )
        
        # Build chatroom context with mock values
        chatroom_context = character_manager.build_chatroom_context(
            guild_name="Example Server",
            emojis=":wave: :heart: :fire:",
            lore="This is example lore text that would be loaded from the server.",
            memories="User loves cats and hates rainy days.\nUser mentioned they work as a developer.",
            user_name="ExampleUser",
            active_users=["Alice", "Bob", "Charlie"]
        )
        
        # Combine both sections for full preview
        full_prompt = f"{system_prompt}\n\n---\n\n{chatroom_context}"
        
        token_estimate = len(full_prompt) // 4
        
        return jsonify({
            'character': character.name,
            'prompt': full_prompt,
            'token_estimate': token_estimate
        })
    except Exception as e:
        return jsonify({'error': str(e)})


# --- Test Provider ---

def _sanitize_error_message(error: Exception) -> str:
    """Sanitize error message to avoid leaking sensitive info."""
    msg = str(error)
    # Remove file paths
    import re
    msg = re.sub(r'[A-Za-z]:\\[^\s]+', '[path]', msg)  # Windows paths
    msg = re.sub(r'/[^\s]+/', '[path]/', msg)  # Unix paths
    # Remove API keys that might be in error messages
    msg = re.sub(r'(api[_-]?key|token|secret|password)[=:]\s*\S+', r'\1=[redacted]', msg, flags=re.IGNORECASE)
    return msg[:200]


@app.route('/api/test-provider/<int:index>')
def api_test_provider(index):
    """Test connection to a specific provider."""
    from openai import OpenAI  # Use sync client to avoid blocking issues

    providers_file = Path("providers.json")
    if not providers_file.exists():
        return jsonify({'success': False, 'error': 'providers.json not found'})

    try:
        with open(providers_file, 'r') as f:
            data = json.load(f)
        providers = data.get('providers', [])
        if index >= len(providers):
            return jsonify({'success': False, 'error': 'Provider index out of range'})

        p = providers[index]
        key_env = p.get('key_env', '')
        key = os.getenv(key_env, 'not-needed') if key_env else 'not-needed'

        # Use sync client instead of asyncio.run() which can block Flask
        client = OpenAI(base_url=p['url'], api_key=key, timeout=10)
        client.models.list()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _sanitize_error_message(e)})


# --- Export/Import ---

@app.route('/settings/export')
def export_config():
    """Export configuration as ZIP."""
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for filename in ['providers.json', 'bots.json']:
            if os.path.exists(filename):
                with open(filename, 'r') as f:
                    zf.writestr(filename, f.read())
        
        autonomous_file = DATA_DIR / 'autonomous.json'
        if autonomous_file.exists():
            with open(autonomous_file, 'r') as f:
                zf.writestr('autonomous.json', f.read())
    
    zip_buffer.seek(0)
    return send_file(zip_buffer, mimetype='application/zip', as_attachment=True, download_name='discord-pals-config.zip')


@app.route('/settings/import', methods=['POST'])
@requires_csrf
def import_config():
    """Import configuration from ZIP with path traversal protection."""
    if 'config_zip' not in request.files:
        return redirect(url_for('settings'))

    file = request.files['config_zip']
    if file.filename == '':
        return redirect(url_for('settings'))

    try:
        with zipfile.ZipFile(io.BytesIO(file.read()), 'r') as zf:
            for entry_name in zf.namelist():
                # Validate entry name against whitelist (prevents path traversal)
                safe_name = validate_zip_entry(entry_name, ALLOWED_IMPORT_FILES)
                if not safe_name:
                    continue  # Skip invalid/disallowed entries

                if safe_name in ['providers.json', 'bots.json']:
                    with open(safe_name, 'wb') as f:
                        f.write(zf.read(entry_name))
                elif safe_name == 'autonomous.json':
                    DATA_DIR.mkdir(exist_ok=True)
                    with open(DATA_DIR / 'autonomous.json', 'wb') as f:
                        f.write(zf.read(entry_name))
    except Exception as e:
        log.warn(f"Failed to import config: {e}")
    
    return redirect(url_for('settings'))


# --- Live Logs ---

@app.route('/logs')
def logs_page():
    """Live logs page (merged with Stats and Context)."""
    from stats import stats_manager
    import runtime_config
    
    stats = stats_manager.get_summary()
    contexts = runtime_config.get_last_context()
    
    return render_template('logs.html', stats=stats, contexts=contexts)


@app.route('/api/logs')
def api_logs():
    """Get recent logs."""
    import logger
    logs = logger.get_logs(100)
    return jsonify(logs)


@app.route('/api/logs/clear', methods=['POST'])
@requires_csrf
def api_clear_logs():
    """Clear the log buffer."""
    import logger
    logger.clear_logs()
    log.info("Logs cleared via dashboard")
    return jsonify({'status': 'ok'})


# --- Channels Management ---

@app.route('/channels')
def channels_page():
    """Channel management page."""
    from discord_utils import autonomous_manager, conversation_history
    
    # Collect all channels from all bots
    channels_data = {}
    guilds_data = {}
    
    for bot in bot_instances:
        if not hasattr(bot, 'client') or not bot.client.is_ready():
            continue
        
        for guild in bot.client.guilds:
            if guild.id not in guilds_data:
                guilds_data[guild.id] = {
                    'id': guild.id,
                    'name': guild.name,
                    'icon': str(guild.icon.url) if guild.icon else None
                }
            
            for channel in guild.text_channels:
                if channel.id not in channels_data:
                    # Check if autonomous is enabled
                    auto_enabled = channel.id in autonomous_manager.enabled_channels
                    auto_chance = autonomous_manager.enabled_channels.get(channel.id, 0)
                    auto_cooldown = autonomous_manager.channel_cooldowns.get(channel.id)
                    cooldown_mins = int(auto_cooldown.total_seconds() // 60) if auto_cooldown else 2
                    allow_bot_triggers = autonomous_manager.allow_bot_triggers.get(channel.id, False)
                    
                    # Check if we have history for this channel
                    history_count = len(conversation_history.get(channel.id, []))
                    
                    channels_data[channel.id] = {
                        'id': channel.id,
                        'name': channel.name,
                        'guild_id': guild.id,
                        'guild_name': guild.name,
                        'autonomous': auto_enabled,
                        'auto_chance': int(auto_chance * 100) if auto_enabled else 5,
                        'auto_cooldown': cooldown_mins,
                        'allow_bot_triggers': allow_bot_triggers,
                        'history_count': history_count
                    }
    
    # Sort channels by autonomous (enabled first), then guild name, then channel name
    sorted_channels = sorted(channels_data.values(), key=lambda c: (not c['autonomous'], c['guild_name'], c['name']))
    
    return render_template('channels.html',
        channels=sorted_channels,
        guilds=list(guilds_data.values())
    )


@app.route('/api/channels')
def api_channels():
    """API endpoint to list all accessible channels."""
    from discord_utils import autonomous_manager, conversation_history
    
    channels = []
    
    for bot in bot_instances:
        if not hasattr(bot, 'client') or not bot.client.is_ready():
            continue
        
        for guild in bot.client.guilds:
            for channel in guild.text_channels:
                # Avoid duplicates
                if any(c['id'] == channel.id for c in channels):
                    continue
                
                auto_enabled = channel.id in autonomous_manager.enabled_channels
                auto_chance = autonomous_manager.enabled_channels.get(channel.id, 0)
                auto_cooldown = autonomous_manager.channel_cooldowns.get(channel.id)
                cooldown_mins = int(auto_cooldown.total_seconds() // 60) if auto_cooldown else 2
                allow_bot_triggers = autonomous_manager.allow_bot_triggers.get(channel.id, False)
                history_count = len(conversation_history.get(channel.id, []))
                
                channels.append({
                    'id': channel.id,
                    'name': channel.name,
                    'guild_id': guild.id,
                    'guild_name': guild.name,
                    'autonomous': auto_enabled,
                    'auto_chance': int(auto_chance * 100) if auto_enabled else 5,
                    'auto_cooldown': cooldown_mins,
                    'allow_bot_triggers': allow_bot_triggers,
                    'history_count': history_count
                })
    
    return jsonify({'channels': channels})


@app.route('/api/channels/<int:channel_id>/clear', methods=['POST'])
@requires_csrf
def api_clear_channel(channel_id):
    """Clear conversation history for a channel."""
    from discord_utils import clear_history
    
    clear_history(channel_id)
    log.info(f"Channel history cleared via dashboard: {channel_id}")
    
    return jsonify({'status': 'ok', 'channel_id': channel_id})


@app.route('/api/channels/<int:channel_id>/autonomous', methods=['GET', 'POST'])
@requires_csrf
def api_channel_autonomous(channel_id):
    """Get or set autonomous mode for a channel."""
    from discord_utils import autonomous_manager
    
    if request.method == 'POST':
        data = request.json or {}
        enabled = data.get('enabled', False)
        chance = data.get('chance', 5)  # percentage
        cooldown = data.get('cooldown', 2)  # minutes
        allow_bot_triggers = data.get('allow_bot_triggers', False)
        
        # Convert percentage to decimal
        chance_decimal = min(100, max(1, chance)) / 100.0
        cooldown_mins = min(10, max(1, cooldown))
        
        autonomous_manager.set_channel(channel_id, enabled, chance_decimal, cooldown_mins, allow_bot_triggers)
        
        if enabled:
            bot_trigger_str = " (bots can trigger)" if allow_bot_triggers else " (humans only)"
            log.info(f"Autonomous mode ENABLED for channel {channel_id} ({chance}%, {cooldown_mins}min cooldown{bot_trigger_str}) via dashboard")
        else:
            log.info(f"Autonomous mode DISABLED for channel {channel_id} via dashboard")
        
        return jsonify({
            'status': 'ok',
            'channel_id': channel_id,
            'enabled': enabled,
            'chance': chance,
            'cooldown': cooldown_mins,
            'allow_bot_triggers': allow_bot_triggers
        })
    
    # GET request
    auto_enabled = channel_id in autonomous_manager.enabled_channels
    auto_chance = autonomous_manager.enabled_channels.get(channel_id, 0)
    auto_cooldown = autonomous_manager.channel_cooldowns.get(channel_id)
    cooldown_mins = int(auto_cooldown.total_seconds() // 60) if auto_cooldown else 2
    allow_bot_triggers = autonomous_manager.allow_bot_triggers.get(channel_id, False)
    
    return jsonify({
        'channel_id': channel_id,
        'enabled': auto_enabled,
        'chance': int(auto_chance * 100) if auto_enabled else 5,
        'cooldown': cooldown_mins,
        'allow_bot_triggers': allow_bot_triggers
    })


# --- Memory Management API ---

@app.route('/api/guilds')
def api_guilds():
    """Get list of all guilds the bots are in."""
    guilds = []
    seen_ids = set()
    
    for bot in bot_instances:
        if not hasattr(bot, 'client') or not bot.client.is_ready():
            continue
        
        for guild in bot.client.guilds:
            if guild.id not in seen_ids:
                seen_ids.add(guild.id)
                guilds.append({
                    'id': guild.id,
                    'name': guild.name,
                    'icon': str(guild.icon.url) if guild.icon else None,
                    'member_count': guild.member_count
                })
    
    return jsonify({'guilds': guilds})


@app.route('/api/memories/add', methods=['POST'])
@requires_csrf
def api_add_memory():
    """Add a new memory."""
    from memory import memory_manager
    
    data = request.json or {}
    memory_type = data.get('type', 'server')  # server, lore, user
    guild_id = data.get('guild_id')
    user_id = data.get('user_id')
    content = data.get('content', '').strip()
    character_name = data.get('character_name')
    
    if not content:
        return jsonify({'status': 'error', 'message': 'Content is required'}), 400
    
    try:
        if memory_type == 'server' and guild_id:
            memory_manager.add_server_memory(int(guild_id), content, auto=False)
            log.info(f"Server memory added via dashboard for guild {guild_id}")
        elif memory_type == 'lore' and guild_id:
            memory_manager.add_lore(int(guild_id), content)
            log.info(f"Lore added via dashboard for guild {guild_id}")
        elif memory_type == 'user' and guild_id and user_id:
            memory_manager.add_user_memory(int(guild_id), int(user_id), content,
                                           auto=False, character_name=character_name)
            log.info(f"User memory added via dashboard for user {user_id} in guild {guild_id}")
        elif memory_type == 'global' and user_id:
            memory_manager.add_global_user_profile(int(user_id), content, auto=False)
            log.info(f"Global user profile added via dashboard for user {user_id}")
        else:
            return jsonify({'status': 'error', 'message': 'Invalid memory type or missing IDs'}), 400
        
        return jsonify({'status': 'ok', 'type': memory_type})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/lore/<int:guild_id>', methods=['GET', 'POST', 'DELETE'])
@requires_csrf
def api_lore(guild_id):
    """Get, set, or delete lore for a guild."""
    from memory import memory_manager

    if request.method == 'GET':
        lore = memory_manager.get_lore(guild_id)
        return jsonify({'guild_id': guild_id, 'lore': lore})

    elif request.method == 'POST':
        data = request.json or {}
        content = data.get('content', '').strip()
        replace = data.get('replace', False)

        if replace:
            # Clear existing lore first
            memory_manager.clear_lore(guild_id)

        if content:
            memory_manager.add_lore(guild_id, content)
            log.info(f"Lore {'replaced' if replace else 'added'} via dashboard for guild {guild_id}")

        return jsonify({'status': 'ok', 'guild_id': guild_id})

    elif request.method == 'DELETE':
        memory_manager.clear_lore(guild_id)
        log.info(f"Lore cleared via dashboard for guild {guild_id}")
        return jsonify({'status': 'ok', 'guild_id': guild_id})


@app.route('/api/memories/<file_name>/delete-selected', methods=['POST'])
@requires_csrf
def api_delete_selected_memories(file_name):
    """Delete multiple selected memory entries from a file."""
    import json

    data = request.json or {}
    keys = data.get('keys', [])

    if not keys:
        return jsonify({'status': 'error', 'message': 'No keys provided'}), 400

    try:
        # Load the memory file
        file_path = DATA_DIR / f"{file_name}.json"
        if not file_path.exists():
            return jsonify({'status': 'error', 'message': 'File not found'}), 404

        with open(file_path, 'r', encoding='utf-8') as f:
            memories = json.load(f)

        # Delete selected keys
        deleted_count = 0
        for key in keys:
            if key in memories:
                del memories[key]
                deleted_count += 1

        # Save back to file
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(memories, f, indent=2, ensure_ascii=False)

        log.info(f"Deleted {deleted_count} memories from {file_name} via dashboard")
        return jsonify({'status': 'ok', 'deleted': deleted_count})

    except Exception as e:
        log.error(f"Error deleting selected memories: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/memories/<file_name>/clear-all', methods=['POST'])
@requires_csrf
def api_clear_all_memories(file_name):
    """Clear all memories from a specific file."""
    import json

    try:
        # Map file names to appropriate clear functions
        if file_name == 'memories':
            # This would clear ALL server memories - need guild_id
            return jsonify({'status': 'error', 'message': 'Cannot clear all server memories without guild_id'}), 400
        elif file_name == 'lore':
            # This would clear ALL lore - need guild_id
            return jsonify({'status': 'error', 'message': 'Cannot clear all lore without guild_id'}), 400
        elif file_name == 'user_profiles':
            # Clear all global user profiles - dangerous!
            file_path = DATA_DIR / f"{file_name}.json"
            if file_path.exists():
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump({}, f)
                log.warn(f"Cleared ALL global user profiles via dashboard")
                return jsonify({'status': 'ok', 'message': 'All global user profiles cleared'})
        else:
            # For other files (dm_memories, user_memories, etc.), clear the entire file
            file_path = DATA_DIR / f"{file_name}.json"
            if file_path.exists():
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump({}, f)
                log.warn(f"Cleared all memories from {file_name} via dashboard")
                return jsonify({'status': 'ok', 'message': f'All memories cleared from {file_name}'})
            else:
                return jsonify({'status': 'error', 'message': 'File not found'}), 404

    except Exception as e:
        log.error(f"Error clearing memories: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


# --- Memory Deduplication API ---

@app.route('/api/memories/deduplicate', methods=['POST'])
@requires_csrf
def api_deduplicate_memories():
    """Remove duplicate memories across all memory stores."""
    from memory import memory_manager, _is_duplicate_memory

    removed_count = 0

    def dedupe_list(memories: list) -> tuple:
        """Deduplicate a list of memories, keeping oldest. Returns (deduped_list, removed_count)."""
        if not memories:
            return memories, 0

        seen = []
        removed = 0
        for mem in memories:
            content = mem.get('content', '')
            if not _is_duplicate_memory(content, seen):
                seen.append(mem)
            else:
                removed += 1
        return seen, removed

    try:
        # Deduplicate server memories
        for guild_id in list(memory_manager.server_memories.keys()):
            deduped, count = dedupe_list(memory_manager.server_memories[guild_id])
            if count > 0:
                memory_manager.server_memories[guild_id] = deduped
                memory_manager._mark_dirty('server')
                removed_count += count

        # Deduplicate global user profiles
        for user_id in list(memory_manager.global_user_profiles.keys()):
            deduped, count = dedupe_list(memory_manager.global_user_profiles[user_id])
            if count > 0:
                memory_manager.global_user_profiles[user_id] = deduped
                memory_manager._mark_dirty('global_profiles')
                removed_count += count

        # Force save
        memory_manager.flush()

        log.info(f"Memory deduplication complete: removed {removed_count} duplicates")
        return jsonify({'status': 'ok', 'removed': removed_count})

    except Exception as e:
        log.error(f"Error during memory deduplication: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


# --- Version API ---

def _get_file_version():
    """Read version from version.py file (may differ from running version after update)."""
    try:
        version_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'version.py')
        with open(version_file, 'r') as f:
            content = f.read()
        # Extract version string
        import re
        match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
        if match:
            return match.group(1)
    except Exception:
        pass
    return VERSION  # Fallback to imported version


def _get_github_repo_info():
    """Extract GitHub owner/repo from git remote origin URL."""
    import subprocess
    try:
        bot_dir = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run(
            ['git', 'remote', 'get-url', 'origin'],
            cwd=bot_dir,
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            # Handle both HTTPS and SSH formats
            # https://github.com/owner/repo.git
            # git@github.com:owner/repo.git
            import re
            match = re.search(r'github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$', url)
            if match:
                return match.group(1), match.group(2)
    except Exception:
        pass
    return None, None


def _compare_versions(v1: str, v2: str) -> int:
    """Compare semantic versions. Returns 1 if v1 > v2, -1 if v1 < v2, 0 if equal."""
    def parse_version(v):
        return [int(x) for x in (v or '').lstrip('v').split('.') if x.isdigit()]
    try:
        parts1, parts2 = parse_version(v1), parse_version(v2)
        max_len = max(len(parts1), len(parts2))
        parts1.extend([0] * (max_len - len(parts1)))
        parts2.extend([0] * (max_len - len(parts2)))
        return (parts1 > parts2) - (parts1 < parts2)
    except (ValueError, AttributeError):
        return 0


def _check_github_latest_version():
    """Check GitHub API for latest release/tag version."""
    import urllib.request
    import json

    owner, repo = _get_github_repo_info()
    if not owner or not repo:
        return None

    # Try releases first, then tags
    urls = [
        f'https://api.github.com/repos/{owner}/{repo}/releases/latest',
        f'https://api.github.com/repos/{owner}/{repo}/tags',
    ]

    for url in urls:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Discord-Pals'})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())

                if 'tag_name' in data:
                    # Latest release response
                    version = data['tag_name'].lstrip('v')
                    return version
                elif isinstance(data, list) and len(data) > 0:
                    # Tags list response
                    version = data[0]['name'].lstrip('v')
                    return version
        except Exception:
            continue

    return None


@app.route('/api/version', methods=['GET'])
@requires_auth
def api_version():
    """Get version information including GitHub latest version."""
    file_version = _get_file_version()
    github_version = _check_github_latest_version()

    # Determine update status
    update_available = False  # New version on GitHub to pull
    restart_required = False  # Already updated locally, just need restart
    latest_version = VERSION

    # Check if GitHub has a newer version than what's on disk
    if github_version and _compare_versions(github_version, file_version) > 0:
        update_available = True
        latest_version = github_version
    # Check if disk version is newer than running version (restart needed)
    elif file_version != VERSION and _compare_versions(file_version, VERSION) > 0:
        restart_required = True
        latest_version = file_version

    return jsonify({
        'status': 'ok',
        'running_version': VERSION,
        'file_version': file_version,
        'github_version': github_version,
        'latest_version': latest_version,
        'update_available': update_available,
        'restart_required': restart_required
    })


# --- Update API ---

@app.route('/api/update', methods=['POST'])
@requires_csrf
@requires_auth
def api_update():
    """Pull latest changes from git repository."""
    import subprocess

    log.info("Git update requested via dashboard")

    try:
        # Get the directory where the bot is running
        bot_dir = os.path.dirname(os.path.abspath(__file__))

        # Run git pull
        result = subprocess.run(
            ['git', 'pull'],
            cwd=bot_dir,
            capture_output=True,
            text=True,
            timeout=60
        )

        output = result.stdout.strip()
        error = result.stderr.strip()

        if result.returncode == 0:
            # Check if already up to date
            if 'Already up to date' in output or 'Already up-to-date' in output:
                log.info("Git pull: Already up to date")
                return jsonify({
                    'status': 'ok',
                    'message': 'Already up to date',
                    'output': output,
                    'updated': False,
                    'running_version': VERSION,
                    'new_version': None
                })
            else:
                # Check if version changed
                new_version = _get_file_version()
                version_changed = new_version != VERSION

                log.info(f"Git pull successful: {output}")
                if version_changed:
                    log.info(f"Version update available: {VERSION} -> {new_version}")

                return jsonify({
                    'status': 'ok',
                    'message': f'Update successful! Restart to apply v{new_version}.' if version_changed else 'Update successful! Restart to apply changes.',
                    'output': output,
                    'updated': True,
                    'running_version': VERSION,
                    'new_version': new_version if version_changed else None
                })
        else:
            log.error(f"Git pull failed: {error or output}")
            return jsonify({
                'status': 'error',
                'message': error or output or 'Git pull failed'
            }), 500

    except subprocess.TimeoutExpired:
        log.error("Git pull timed out")
        return jsonify({
            'status': 'error',
            'message': 'Update timed out after 60 seconds'
        }), 500
    except FileNotFoundError:
        log.error("Git not found")
        return jsonify({
            'status': 'error',
            'message': 'Git is not installed or not in PATH'
        }), 500
    except Exception as e:
        log.error(f"Update error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# --- Restart API ---

@app.route('/api/restart', methods=['POST'])
@requires_csrf
@requires_auth
def api_restart():
    """Restart the bot application without terminal restart."""
    import sys
    import subprocess

    log.warn("Application restart requested via dashboard")

    # Save current state before restart
    try:
        from discord_utils import save_history
        from memory import memory_manager
        save_history()
        memory_manager.save_all()
        log.info("State saved before restart")
    except Exception as e:
        log.error(f"Failed to save state before restart: {e}")

    def do_restart():
        """Perform the actual restart in a separate thread."""
        import time
        import signal
        time.sleep(1)  # Give time for response to be sent

        # Check if running under systemd (simplified detection - don't require INVOCATION_ID)
        is_systemd = os.path.exists('/run/systemd/system')
        service_name = 'discord-pals'

        if is_systemd:
            log.info("Detected systemd environment, attempting restart")

            # Try multiple approaches in order of preference
            restart_commands = [
                ['systemctl', '--user', 'restart', service_name],      # User service
                ['systemctl', 'restart', service_name],                 # System service (if running as root)
                ['sudo', '-n', 'systemctl', 'restart', service_name],  # With passwordless sudo
            ]

            for cmd in restart_commands:
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                    if result.returncode == 0:
                        log.info(f"Restart successful with: {' '.join(cmd)}")
                        return
                    log.debug(f"Command {cmd} failed: {result.stderr}")
                except Exception as e:
                    log.debug(f"Command {cmd} error: {e}")
                    continue

            log.warn("All systemctl restart attempts failed, falling back to SIGTERM")

            # Fallback: Send SIGTERM - systemd will restart us (if Restart=always)
            log.info("Sending SIGTERM for systemd to restart us")
            os.kill(os.getpid(), signal.SIGTERM)
            return

        # Non-systemd fallback: Direct process restart
        # Get the current Python executable and script
        python = sys.executable
        script = os.path.abspath(sys.argv[0])

        # Close all bot connections gracefully
        import asyncio
        for bot in bot_instances:
            try:
                asyncio.run_coroutine_threadsafe(bot.close(), bot.client.loop)
            except Exception:
                pass

        time.sleep(2)  # Wait for connections to close
        # Restart the process
        os.execv(python, [python, script] + sys.argv[1:])

    # Start restart in background thread
    restart_thread = threading.Thread(target=do_restart, daemon=True)
    restart_thread.start()

    return jsonify({
        'status': 'ok',
        'message': 'Restart initiated. The dashboard will be unavailable for a few seconds.'
    })


# --- Dashboard Runner ---

def start_dashboard(bots=None, host='127.0.0.1', port=5000):
    """Start the dashboard in a background thread using Waitress production server."""
    global bot_instances
    if bots:
        bot_instances = bots

    # Disable Flask's default logging
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)

    # Use Waitress production WSGI server instead of Flask's dev server
    # Waitress is thread-safe and handles concurrent requests properly
    from waitress import serve

    thread = threading.Thread(
        target=lambda: serve(app, host=host, port=port, threads=8, _quiet=True),
        daemon=True
    )
    thread.start()
    return thread
