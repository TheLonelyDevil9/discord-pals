"""
Discord Pals - Web Dashboard
Local web interface for managing bot, memories, and characters.
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file
import threading
import json
import os
import io
import zipfile
from pathlib import Path

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = 'discord-pals-local-dashboard'

# Shared state (set by main.py)
bot_instances = []
DATA_DIR = Path("bot_data")
CHARACTERS_DIR = Path("characters")


def get_memory_files():
    """Get all memory JSON files."""
    files = {}
    if DATA_DIR.exists():
        for f in DATA_DIR.glob("*.json"):
            if f.name != "autonomous.json":
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
        if bot_activity:
            elapsed = time.time() - bot_activity
            if elapsed < 60:
                activity_str = f"{int(elapsed)}s ago"
            elif elapsed < 3600:
                activity_str = f"{int(elapsed/60)}m ago"
            else:
                activity_str = f"{int(elapsed/3600)}h ago"
        else:
            activity_str = "Never"
        
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
    
    return render_template('dashboard.html',
        bots=bots_info,
        memory_count=memory_count,
        character_count=character_count,
        autonomous_count=autonomous_count,
        global_paused=global_paused,
        bot_interactions_paused=bot_interactions_paused
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
def delete_memory(name):
    """Delete a specific memory entry."""
    file_path = DATA_DIR / f"{name}.json"
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
            print(f"Warning: Failed to delete memory '{memory_key}': {e}")
    
    return redirect(url_for('memories'))


@app.route('/memories/<name>/edit')
def edit_memory(name):
    """Edit a memory file."""
    file_path = DATA_DIR / f"{name}.json"
    content = "{}"
    
    if file_path.exists():
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            print(f"Warning: Failed to read memory file: {e}")
    
    return render_template('memory_edit.html', name=name, content=content)


@app.route('/memories/<name>/save', methods=['POST'])
def save_memory(name):
    """Save memory file changes."""
    file_path = DATA_DIR / f"{name}.json"
    content = request.form.get('content', '{}')
    
    try:
        json.loads(content)  # Validate JSON
        DATA_DIR.mkdir(exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        print(f"Warning: Failed to save memory: {e}")
    
    return redirect(url_for('memories'))


# --- Characters ---

@app.route('/characters')
def characters():
    """Characters viewer page."""
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
    
    return render_template('characters.html', characters=chars_data)


@app.route('/characters/<name>/edit')
def edit_character(name):
    """Edit a character file."""
    path = CHARACTERS_DIR / f"{name}.md"
    content = ""
    
    if path.exists():
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            print(f"Warning: Failed to read character file: {e}")
    
    return render_template('character_edit.html', name=name, content=content)


@app.route('/characters/<name>/save', methods=['POST'])
def save_character(name):
    """Save character file changes."""
    path = CHARACTERS_DIR / f"{name}.md"
    content = request.form.get('content', '')
    
    try:
        CHARACTERS_DIR.mkdir(exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        print(f"Warning: Failed to save character: {e}")
    
    return redirect(url_for('characters'))


@app.route('/characters/<name>/delete', methods=['POST'])
def delete_character(name):
    """Delete a character file."""
    path = CHARACTERS_DIR / f"{name}.md"
    
    if path.exists():
        try:
            path.unlink()
        except Exception as e:
            print(f"Warning: Failed to delete character: {e}")
    
    return redirect(url_for('characters'))


@app.route('/characters/new', methods=['POST'])
def new_character():
    """Create a new character file."""
    name = request.form.get('name', '').strip()
    
    if name:
        path = CHARACTERS_DIR / f"{name}.md"
        if not path.exists():
            try:
                CHARACTERS_DIR.mkdir(exist_ok=True)
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(f"# {name}\n\n## Personality\n\n## Backstory\n\n## Relationships\n")
            except Exception as e:
                print(f"Warning: Failed to create character: {e}")
        return redirect(url_for('edit_character', name=name))
    
    return redirect(url_for('characters'))


# --- Settings ---

@app.route('/settings')
def settings():
    """Settings editor page."""
    providers_raw = "{}"
    bots_raw = "{}"
    autonomous_raw = "{}"
    
    if os.path.exists('providers.json'):
        try:
            with open('providers.json', 'r') as f:
                providers_raw = f.read()
        except Exception as e:
            print(f"Warning: Failed to read providers.json: {e}")
    
    if os.path.exists('bots.json'):
        try:
            with open('bots.json', 'r') as f:
                bots_raw = f.read()
        except Exception as e:
            print(f"Warning: Failed to read bots.json: {e}")
    
    autonomous_file = DATA_DIR / 'autonomous.json'
    if autonomous_file.exists():
        try:
            with open(autonomous_file, 'r') as f:
                autonomous_raw = f.read()
        except Exception as e:
            print(f"Warning: Failed to read autonomous.json: {e}")
    
    return render_template('settings.html',
        providers_raw=providers_raw,
        bots_raw=bots_raw,
        autonomous_raw=autonomous_raw,
        message=request.args.get('message'),
        error=request.args.get('error')
    )


@app.route('/settings/providers/save', methods=['POST'])
def save_providers():
    """Save providers.json."""
    content = request.form.get('content', '')
    try:
        json.loads(content)  # Validate JSON
        with open('providers.json', 'w') as f:
            f.write(content)
        return redirect(url_for('settings', message='Providers saved successfully'))
    except json.JSONDecodeError as e:
        import logger as log
        log.error(f"Failed to save providers.json: Invalid JSON - {e}")
        return redirect(url_for('settings', error=f'Invalid JSON: {e}'))
    except Exception as e:
        import logger as log
        log.error(f"Failed to save providers.json: {e}")
        return redirect(url_for('settings', error=f'Save failed: {e}'))


@app.route('/settings/bots/save', methods=['POST'])
def save_bots():
    """Save bots.json."""
    content = request.form.get('content', '')
    try:
        json.loads(content)  # Validate JSON
        with open('bots.json', 'w') as f:
            f.write(content)
        return redirect(url_for('settings', message='Bots config saved successfully'))
    except json.JSONDecodeError as e:
        import logger as log
        log.error(f"Failed to save bots.json: Invalid JSON - {e}")
        return redirect(url_for('settings', error=f'Invalid JSON: {e}'))
    except Exception as e:
        import logger as log
        log.error(f"Failed to save bots.json: {e}")
        return redirect(url_for('settings', error=f'Save failed: {e}'))


@app.route('/settings/autonomous/save', methods=['POST'])
def save_autonomous():
    """Save autonomous.json."""
    content = request.form.get('content', '')
    try:
        json.loads(content)  # Validate JSON
        DATA_DIR.mkdir(exist_ok=True)
        with open(DATA_DIR / 'autonomous.json', 'w') as f:
            f.write(content)
        return redirect(url_for('settings', message='Autonomous config saved successfully'))
    except json.JSONDecodeError as e:
        import logger as log
        log.error(f"Failed to save autonomous.json: Invalid JSON - {e}")
        return redirect(url_for('settings', error=f'Invalid JSON: {e}'))
    except Exception as e:
        import logger as log
        log.error(f"Failed to save autonomous.json: {e}")
        return redirect(url_for('settings', error=f'Save failed: {e}'))


# --- Prompts ---

PROMPTS_DIR = Path("prompts")

@app.route('/prompts')
def prompts():
    """Prompts editor page."""
    system_content = ""
    rules_content = ""
    
    system_path = PROMPTS_DIR / "system.md"
    rules_path = PROMPTS_DIR / "response_rules.md"
    
    if system_path.exists():
        try:
            with open(system_path, 'r', encoding='utf-8') as f:
                system_content = f.read()
        except Exception as e:
            print(f"Warning: Failed to read system.md: {e}")
    
    if rules_path.exists():
        try:
            with open(rules_path, 'r', encoding='utf-8') as f:
                rules_content = f.read()
        except Exception as e:
            print(f"Warning: Failed to read response_rules.md: {e}")
    
    return render_template('prompts.html',
        system_content=system_content,
        rules_content=rules_content
    )


@app.route('/prompts/system/save', methods=['POST'])
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
        print(f"Warning: Failed to save system.md: {e}")
    return redirect(url_for('prompts'))


@app.route('/api/status')
def api_status():
    """API endpoint for bot status with extended info."""
    import time
    import runtime_config
    
    last_activity = runtime_config.get_last_activity()
    bots_info = []
    for bot in bot_instances:
        bot_activity = last_activity.get(bot.name)
        if bot_activity:
            elapsed = time.time() - bot_activity
            if elapsed < 60:
                activity_str = f"{int(elapsed)}s ago"
            elif elapsed < 3600:
                activity_str = f"{int(elapsed/60)}m ago"
            else:
                activity_str = f"{int(elapsed/3600)}h ago"
        else:
            activity_str = "Never"
        
        bots_info.append({
            'name': bot.name,
            'character': bot.character.name if bot.character else None,
            'online': bot.client.is_ready() if hasattr(bot, 'client') else False,
            'last_activity': activity_str
        })
    
    # Include global state
    global_paused = runtime_config.get("global_paused", False)
    bot_interactions_paused = runtime_config.get("bot_interactions_paused", False)
    
    return jsonify({
        'bots': bots_info,
        'global_paused': global_paused,
        'bot_interactions_paused': bot_interactions_paused
    })


@app.route('/api/killswitch', methods=['GET', 'POST'])
def api_killswitch():
    """API endpoint for global killswitch control."""
    import runtime_config
    import logger as log
    
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
def api_bot_interactions():
    """API endpoint for bot-to-bot interaction control."""
    import runtime_config
    import logger as log
    
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
            log.info("Bot-to-bot interactions RESUMED v")
        
        return jsonify({
            'status': 'ok',
            'bot_interactions_paused': new_state
        })
    
    # GET request
    return jsonify({
        'bot_interactions_paused': runtime_config.get("bot_interactions_paused", False)
    })


# --- Runtime Config ---

@app.route('/config')
def config_page():
    """Runtime configuration page."""
    import runtime_config
    
    config = runtime_config.get_all()
    characters = get_character_files()
    
    # Get providers
    providers = []
    providers_file = Path("providers.json")
    if providers_file.exists():
        try:
            with open(providers_file, 'r') as f:
                data = json.load(f)
                providers = [p.get('name', f"Provider {i}") for i, p in enumerate(data.get('providers', []))]
        except Exception as e:
            print(f"Warning: Failed to load providers for config page: {e}")
    
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
            'auto_channels': auto_channels
        })
    
    return render_template('config.html',
        config=config,
        characters=characters,
        providers=providers,
        bots=bots_info
    )


@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    """API for runtime config."""
    import runtime_config
    
    if request.method == 'POST':
        data = request.json
        for key, value in data.items():
            runtime_config.set(key, value)
        return jsonify({'status': 'ok'})
    
    return jsonify(runtime_config.get_all())


@app.route('/api/switch_character', methods=['POST'])
def api_switch_character():
    """Switch character for a bot."""
    data = request.json
    bot_name = data.get('bot_name')
    character_name = data.get('character')
    
    for bot in bot_instances:
        if bot.name == bot_name:
            try:
                from character import character_manager
                bot.character = character_manager.load_character(character_name)
                return jsonify({'status': 'ok', 'character': bot.character.name})
            except Exception as e:
                return jsonify({'status': 'error', 'message': str(e)}), 400
    
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

@app.route('/api/test-provider/<int:index>')
def api_test_provider(index):
    """Test connection to a specific provider."""
    import asyncio
    from openai import AsyncOpenAI
    
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
        
        client = AsyncOpenAI(base_url=p['url'], api_key=key, timeout=10)
        
        async def test():
            response = await client.models.list()
            return True
        
        asyncio.run(test())
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:200]})


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
def import_config():
    """Import configuration from ZIP."""
    if 'config_zip' not in request.files:
        return redirect(url_for('settings'))
    
    file = request.files['config_zip']
    if file.filename == '':
        return redirect(url_for('settings'))
    
    try:
        with zipfile.ZipFile(io.BytesIO(file.read()), 'r') as zf:
            for name in zf.namelist():
                if name in ['providers.json', 'bots.json']:
                    with open(name, 'wb') as f:
                        f.write(zf.read(name))
                elif name == 'autonomous.json':
                    DATA_DIR.mkdir(exist_ok=True)
                    with open(DATA_DIR / 'autonomous.json', 'wb') as f:
                        f.write(zf.read(name))
    except Exception as e:
        pass
    
    return redirect(url_for('settings'))


# --- Live Logs ---

@app.route('/logs')
def logs_page():
    """Live logs page."""
    return render_template('logs.html')


@app.route('/api/logs')
def api_logs():
    """Get recent logs."""
    import logger
    logs = logger.get_logs(100)
    return jsonify(logs)


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
                        'history_count': history_count
                    }
    
    # Sort channels by guild name, then channel name
    sorted_channels = sorted(channels_data.values(), key=lambda c: (c['guild_name'], c['name']))
    
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
                history_count = len(conversation_history.get(channel.id, []))
                
                channels.append({
                    'id': channel.id,
                    'name': channel.name,
                    'guild_id': guild.id,
                    'guild_name': guild.name,
                    'autonomous': auto_enabled,
                    'auto_chance': int(auto_chance * 100) if auto_enabled else 5,
                    'auto_cooldown': cooldown_mins,
                    'history_count': history_count
                })
    
    return jsonify({'channels': channels})


@app.route('/api/channels/<int:channel_id>/clear', methods=['POST'])
def api_clear_channel(channel_id):
    """Clear conversation history for a channel."""
    from discord_utils import clear_history
    import logger as log
    
    clear_history(channel_id)
    log.info(f"Channel history cleared via dashboard: {channel_id}")
    
    return jsonify({'status': 'ok', 'channel_id': channel_id})


@app.route('/api/channels/<int:channel_id>/autonomous', methods=['GET', 'POST'])
def api_channel_autonomous(channel_id):
    """Get or set autonomous mode for a channel."""
    from discord_utils import autonomous_manager
    import logger as log
    
    if request.method == 'POST':
        data = request.json or {}
        enabled = data.get('enabled', False)
        chance = data.get('chance', 5)  # percentage
        cooldown = data.get('cooldown', 2)  # minutes
        
        # Convert percentage to decimal
        chance_decimal = min(100, max(1, chance)) / 100.0
        cooldown_mins = min(10, max(1, cooldown))
        
        autonomous_manager.set_channel(channel_id, enabled, chance_decimal, cooldown_mins)
        
        if enabled:
            log.info(f"Autonomous mode ENABLED for channel {channel_id} ({chance}%, {cooldown_mins}min cooldown) via dashboard")
        else:
            log.info(f"Autonomous mode DISABLED for channel {channel_id} via dashboard")
        
        return jsonify({
            'status': 'ok',
            'channel_id': channel_id,
            'enabled': enabled,
            'chance': chance,
            'cooldown': cooldown_mins
        })
    
    # GET request
    auto_enabled = channel_id in autonomous_manager.enabled_channels
    auto_chance = autonomous_manager.enabled_channels.get(channel_id, 0)
    auto_cooldown = autonomous_manager.channel_cooldowns.get(channel_id)
    cooldown_mins = int(auto_cooldown.total_seconds() // 60) if auto_cooldown else 2
    
    return jsonify({
        'channel_id': channel_id,
        'enabled': auto_enabled,
        'chance': int(auto_chance * 100) if auto_enabled else 5,
        'cooldown': cooldown_mins
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
def api_add_memory():
    """Add a new memory."""
    from memory import memory_manager
    import logger as log
    
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
def api_lore(guild_id):
    """Get, set, or delete lore for a guild."""
    from memory import memory_manager
    import logger as log
    
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


# --- Dashboard Runner ---

def start_dashboard(bots=None, host='127.0.0.1', port=5000):
    """Start the dashboard in a background thread."""
    global bot_instances
    if bots:
        bot_instances = bots
    
    # Disable Flask's default logging
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    thread = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
        daemon=True
    )
    thread.start()
    return thread
