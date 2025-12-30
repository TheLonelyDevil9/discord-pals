"""
Discord Pals - Web Dashboard
Local web interface for managing bot, memories, and characters.
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for
import threading
import json
import os
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
    bots_info = []
    for bot in bot_instances:
        bots_info.append({
            'name': bot.name,
            'character': bot.character.name if bot.character else 'None',
            'online': bot.client.is_ready() if hasattr(bot, 'client') else False
        })
    
    memory_count = len(get_memory_files())
    character_count = len(get_character_files())
    
    # Get autonomous channels count
    from discord_utils import autonomous_manager
    autonomous_count = len(autonomous_manager.enabled_channels)
    
    return render_template('dashboard.html',
        bots=bots_info,
        memory_count=memory_count,
        character_count=character_count,
        autonomous_count=autonomous_count
    )


@app.route('/memories')
def memories():
    """Memories management page."""
    files = get_memory_files()
    memories_data = {}
    
    for name, path in files.items():
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                memories_data[name] = data
        except Exception as e:
            memories_data[name] = {"error": str(e)}
    
    return render_template('memories.html', memories=memories_data)


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
        except:
            pass
    
    return redirect(url_for('memories'))


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
                # Get first 500 chars as preview
                chars_data[name] = {
                    'preview': content[:500] + ('...' if len(content) > 500 else ''),
                    'full_path': str(path.absolute()),
                    'size': len(content)
                }
        except Exception as e:
            chars_data[name] = {'error': str(e)}
    
    return render_template('characters.html', characters=chars_data)


@app.route('/settings')
def settings():
    """Settings viewer page (read-only)."""
    config_files = {}
    
    # Read providers.json if exists
    if os.path.exists('providers.json'):
        try:
            with open('providers.json', 'r') as f:
                data = json.load(f)
                # Mask API keys
                for provider in data.get('providers', []):
                    if 'key_env' in provider:
                        provider['key_env'] = provider['key_env'] + ' (hidden)'
                config_files['providers'] = data
        except:
            config_files['providers'] = {'error': 'Could not read'}
    
    # Read bots.json if exists
    if os.path.exists('bots.json'):
        try:
            with open('bots.json', 'r') as f:
                data = json.load(f)
                # Mask tokens
                for bot in data.get('bots', []):
                    if 'token_env' in bot:
                        bot['token_env'] = bot['token_env'] + ' (hidden)'
                config_files['bots'] = data
        except:
            config_files['bots'] = {'error': 'Could not read'}
    
    # Get autonomous settings
    autonomous_file = DATA_DIR / 'autonomous.json'
    if autonomous_file.exists():
        try:
            with open(autonomous_file, 'r') as f:
                config_files['autonomous'] = json.load(f)
        except:
            pass
    
    return render_template('settings.html', configs=config_files)


@app.route('/api/status')
def api_status():
    """API endpoint for bot status."""
    bots_info = []
    for bot in bot_instances:
        bots_info.append({
            'name': bot.name,
            'character': bot.character.name if bot.character else None,
            'online': bot.client.is_ready() if hasattr(bot, 'client') else False
        })
    return jsonify({'bots': bots_info})


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
