"""
Discord Pals - Startup Validation
Ensures configuration is valid before bot starts.
Provides helpful error messages and auto-setup guidance.
"""

import os
import sys
import json
import shutil
from pathlib import Path
from typing import List, Tuple, Optional

# Colors for terminal
class Colors:
    OK = '\033[92m'
    WARN = '\033[93m'
    FAIL = '\033[91m'
    INFO = '\033[94m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    END = '\033[0m'

def ok(msg): print(f"{Colors.OK}✓{Colors.END} {msg}")
def warn(msg): print(f"{Colors.WARN}⚠{Colors.END} {msg}")
def fail(msg): print(f"{Colors.FAIL}✗{Colors.END} {msg}")
def info(msg): print(f"{Colors.INFO}ℹ{Colors.END} {msg}")


BASE_DIR = Path(__file__).parent


def check_env_file() -> Tuple[bool, List[str]]:
    """Check if .env file exists, offer to create from example."""
    env_file = BASE_DIR / ".env"
    env_example = BASE_DIR / ".env.example"
    issues = []
    
    if env_file.exists():
        ok(".env file found")
        return True, issues
    
    if env_example.exists():
        fail(".env file missing!")
        print(f"\n{Colors.BOLD}Would you like to create .env from .env.example?{Colors.END}")
        print(f"{Colors.DIM}(You'll need to edit it with your tokens after){Colors.END}")
        
        try:
            response = input("\nCreate .env? [Y/n]: ").strip().lower()
            if response in ('', 'y', 'yes'):
                shutil.copy(env_example, env_file)
                ok(f"Created .env from .env.example")
                warn("Please edit .env and add your DISCORD_TOKEN, then restart!")
                issues.append("new .env created - needs editing")
            else:
                issues.append("missing .env")
        except (EOFError, KeyboardInterrupt):
            issues.append("missing .env")
    else:
        fail(".env file missing and no .env.example found!")
        issues.append("missing .env")
    
    return len(issues) == 0, issues


def _env_secret_missing(value: Optional[str]) -> bool:
    """Return True when an env value is empty or still an obvious placeholder."""
    if value is None:
        return True

    stripped = value.strip()
    if not stripped:
        return True

    lowered = stripped.lower()
    placeholders = {
        "your_token_here",
        "your_discord_token_here",
        "your_discord_bot_token_here",
        "token_for_firefly_bot",
        "token_for_george_bot",
    }
    return lowered in placeholders or lowered.startswith("your_") or lowered.startswith("token_for_")


def _load_env_values() -> dict:
    """Read values exactly from .env so missing multi-bot vars can be reported."""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return {}

    try:
        from dotenv import dotenv_values
    except ImportError:
        dotenv_values = None

    if dotenv_values:
        return {
            key: "" if value is None else str(value)
            for key, value in dotenv_values(env_file).items()
            if key
        }

    values = {}
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip().strip('"').strip("'")
    return values


def check_bots_config() -> Tuple[bool, List[str]]:
    """Validate bots.json and required multi-bot token env vars when present."""
    bots_file = BASE_DIR / "bots.json"
    issues = []

    if not bots_file.exists():
        ok("bots.json not found - single-bot mode")
        return True, issues

    ok("bots.json found")

    try:
        with open(bots_file, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        fail(f"bots.json is invalid JSON: {e}")
        return False, ["invalid bots.json"]
    except OSError as e:
        fail(f"Could not read bots.json: {e}")
        return False, ["invalid bots.json"]

    if not isinstance(data, dict):
        fail('bots.json must contain an object with a "bots" array')
        return False, ["invalid bots.json: root must be object"]

    bots = data.get("bots")
    if not isinstance(bots, list):
        fail('bots.json must contain a "bots" array')
        return False, ['invalid bots.json: missing "bots" array']

    if not bots:
        warn("bots.json has no bots configured")
        return False, ["invalid bots.json: empty bots list"]

    ok(f"  {len(bots)} bot(s) configured")

    env_values = _load_env_values()
    required_fields = ("name", "token_env", "character")

    for index, bot_cfg in enumerate(bots, start=1):
        if not isinstance(bot_cfg, dict):
            warn(f"  [Bot {index}] Entry must be an object")
            issues.append(f"invalid bots.json: bot {index} entry must be an object")
            continue

        missing_fields = [
            field
            for field in required_fields
            if not isinstance(bot_cfg.get(field), str) or not bot_cfg.get(field).strip()
        ]
        if missing_fields:
            fields = ", ".join(missing_fields)
            warn(f"  [Bot {index}] Missing required field(s): {fields}")
            issues.append(f"invalid bots.json: bot {index} missing {fields}")
            continue

        name = bot_cfg["name"].strip()
        token_env = bot_cfg["token_env"].strip()
        character = bot_cfg["character"].strip()
        token_value = env_values.get(token_env)

        if token_env not in env_values:
            warn(f"  [{name}] {token_env} missing from .env")
            issues.append(f"{name}: missing {token_env}")
        elif _env_secret_missing(token_value):
            warn(f"  [{name}] {token_env} is empty or still a placeholder in .env")
            issues.append(f"{name}: missing {token_env}")
        else:
            ok(f"  [{name}] token env: {token_env}")

        ok(f"  [{name}] character: {character}")

    return len(issues) == 0, issues


def check_providers_config() -> Tuple[bool, List[str]]:
    """Check providers.json, offer to create from example."""
    providers_file = BASE_DIR / "providers.json"
    providers_example = BASE_DIR / "providers.json.example"
    issues = []
    
    if providers_file.exists():
        ok("providers.json found")
        
        # Validate JSON
        try:
            with open(providers_file) as f:
                data = json.load(f)
            
            providers = data.get("providers", [])
            if not providers:
                warn("providers.json has no providers configured!")
                issues.append("empty providers list")
            else:
                ok(f"  {len(providers)} provider(s) configured")

                # Check each provider
                from dotenv import load_dotenv
                load_dotenv(BASE_DIR / ".env")

                for i, p in enumerate(providers):
                    name = p.get("name", f"Provider {i+1}")
                    url = p.get("url", "")
                    key_env = p.get("key_env", "")
                    requires_key = p.get("requires_key", True)  # NEW: optional flag

                    # Handle common mistake: "not-needed" as key_env instead of as value
                    if key_env.lower() in ("not-needed", "not_needed", "none", ""):
                        requires_key = False
                        key_env = ""

                    if not url:
                        warn(f"  [{name}] No URL configured")
                        issues.append(f"{name}: no URL")

                    # Check API key (unless explicitly marked as not required)
                    if requires_key and key_env:
                        key_value = os.getenv(key_env, "")
                        if key_value:
                            ok(f"  [{name}] URL: {url[:40]}... | Key: ✓")
                        else:
                            warn(f"  [{name}] {key_env} not set in .env")
                            issues.append(f"{name}: missing {key_env}")
                    elif not requires_key:
                        ok(f"  [{name}] URL: {url[:40]}... | Key: not required")
                    else:
                        ok(f"  [{name}] URL: {url[:40]}... | Key: using placeholder")
                
        except json.JSONDecodeError as e:
            fail(f"providers.json is invalid JSON: {e}")
            issues.append("invalid providers.json")
        
        return len(issues) == 0, issues
    
    # No providers.json - offer to create
    if providers_example.exists():
        fail("providers.json missing!")
        print(f"\n{Colors.BOLD}Would you like to create providers.json?{Colors.END}")
        print("\nOptions:")
        print("  1) Create from example (for cloud APIs like OpenAI)")
        print("  2) Create for local LLM (llama.cpp/Ollama/LM Studio)")
        print("  3) Skip (I'll create it manually)")
        
        try:
            choice = input("\nChoice [1/2/3]: ").strip()
            
            if choice == "1":
                shutil.copy(providers_example, providers_file)
                ok("Created providers.json from example")
                warn("Edit providers.json and add your API keys to .env")
                issues.append("new providers.json - needs configuration")
                
            elif choice == "2":
                # Interactive local LLM setup
                print(f"\n{Colors.BOLD}Local LLM Setup{Colors.END}")
                
                url = input("Enter your LLM server URL (e.g., http://localhost:1234/v1): ").strip()
                if not url:
                    url = "http://localhost:1234/v1"
                
                model = input("Enter model name (or press Enter for 'local-model'): ").strip()
                if not model:
                    model = "local-model"
                
                name = input("Enter a name for this provider (or press Enter for 'Local LLM'): ").strip()
                if not name:
                    name = "Local LLM"
                
                local_config = {
                    "providers": [
                        {
                            "name": name,
                            "url": url,
                            "key_env": "LOCAL_API_KEY",
                            "model": model,
                            "requires_key": False
                        }
                    ],
                    "timeout": 120
                }
                
                with open(providers_file, 'w') as f:
                    json.dump(local_config, f, indent=2)
                
                ok(f"Created providers.json for {name}")
                ok(f"  URL: {url}")
                ok(f"  Model: {model}")
                
                # Also ensure LOCAL_API_KEY has a placeholder in .env
                env_file = BASE_DIR / ".env"
                if env_file.exists():
                    with open(env_file, 'a') as f:
                        f.write("\n# Local LLM (placeholder - not actually needed)\n")
                        f.write("LOCAL_API_KEY=not-needed\n")
                    ok("Added LOCAL_API_KEY placeholder to .env")
                
            else:
                issues.append("missing providers.json")
                
        except (EOFError, KeyboardInterrupt):
            print()
            issues.append("missing providers.json")
    else:
        fail("providers.json missing and no example found!")
        issues.append("missing providers.json")
    
    return len(issues) == 0, issues


def check_discord_token() -> Tuple[bool, List[str]]:
    """Check if DISCORD_TOKEN is set."""
    if (BASE_DIR / "bots.json").exists():
        ok("bots.json present - DISCORD_TOKEN not required in multi-bot mode")
        return True, []

    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
    
    token = os.getenv("DISCORD_TOKEN")
    
    if token:
        # Basic validation - Discord tokens have a specific format
        if len(token) > 50 and '.' in token:
            ok("DISCORD_TOKEN is set")
            return True, []
        else:
            warn("DISCORD_TOKEN looks invalid (too short or wrong format)")
            return False, ["DISCORD_TOKEN looks invalid"]
    else:
        fail("DISCORD_TOKEN not set in .env!")
        return False, ["missing DISCORD_TOKEN"]


def check_characters() -> Tuple[bool, List[str]]:
    """Check if any characters are available."""
    chars_dir = BASE_DIR / "characters"
    issues = []
    
    if not chars_dir.exists():
        fail("characters/ directory not found!")
        return False, ["missing characters directory"]
    
    # Look for .md files (not .json - characters are markdown)
    char_files = list(chars_dir.glob("*.md"))
    # Exclude template.md
    char_files = [f for f in char_files if f.name != "template.md"]
    
    if char_files:
        ok(f"Found {len(char_files)} character(s): {[f.stem for f in char_files]}")
        return True, []
    else:
        warn("No character files found in characters/")
        return False, ["no characters defined"]


def validate_startup(interactive: bool = True) -> bool:
    """
    Run all startup validation checks.
    
    Args:
        interactive: If True, prompt user to fix issues. If False, just report.
    
    Returns:
        True if all checks pass, False otherwise.
    """
    print(f"\n{Colors.BOLD}{'='*50}")
    print("Discord Pals - Startup Validation")
    print(f"{'='*50}{Colors.END}\n")
    
    all_issues = []
    
    # 1. Check .env file
    print(f"{Colors.BOLD}[1/5] Environment File{Colors.END}")
    passed, issues = check_env_file()
    all_issues.extend(issues)
    
    # 2. Check bots.json
    print(f"\n{Colors.BOLD}[2/5] Bot Configuration{Colors.END}")
    passed, issues = check_bots_config()
    all_issues.extend(issues)

    # 3. Check providers.json
    print(f"\n{Colors.BOLD}[3/5] Provider Configuration{Colors.END}")
    passed, issues = check_providers_config()
    all_issues.extend(issues)
    
    # 4. Check Discord token
    print(f"\n{Colors.BOLD}[4/5] Discord Token{Colors.END}")
    passed, issues = check_discord_token()
    all_issues.extend(issues)
    
    # 5. Check characters
    print(f"\n{Colors.BOLD}[5/5] Characters{Colors.END}")
    passed, issues = check_characters()
    all_issues.extend(issues)
    
    # Summary
    print(f"\n{Colors.BOLD}{'='*50}{Colors.END}")
    
    critical_issues = [i for i in all_issues if 'missing' in i.lower() or 'invalid' in i.lower()]
    
    if not all_issues:
        print(f"{Colors.OK}{Colors.BOLD}✓ All checks passed! Starting bot...{Colors.END}")
        return True
    elif critical_issues:
        print(f"{Colors.FAIL}{Colors.BOLD}✗ {len(critical_issues)} critical issue(s) found:{Colors.END}")
        for issue in critical_issues:
            print(f"  • {issue}")
        print(f"\n{Colors.WARN}Please fix these issues and try again.{Colors.END}")
        return False
    else:
        print(f"{Colors.WARN}{Colors.BOLD}⚠ {len(all_issues)} warning(s):{Colors.END}")
        for issue in all_issues:
            print(f"  • {issue}")
        print(f"\n{Colors.INFO}Proceeding with warnings...{Colors.END}")
        return True


if __name__ == "__main__":
    # Run standalone validation
    success = validate_startup(interactive=True)
    sys.exit(0 if success else 1)
