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
                load_dotenv()
                
                for i, p in enumerate(providers):
                    name = p.get("name", f"Provider {i+1}")
                    url = p.get("url", "")
                    key_env = p.get("key_env", "")
                    requires_key = p.get("requires_key", True)  # NEW: optional flag
                    
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
    from dotenv import load_dotenv
    load_dotenv()
    
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
    
    # Look for .json files
    char_files = list(chars_dir.glob("*.json"))
    
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
    print(f"{Colors.BOLD}[1/4] Configuration Files{Colors.END}")
    passed, issues = check_env_file()
    all_issues.extend(issues)
    
    # 2. Check providers.json
    print(f"\n{Colors.BOLD}[2/4] Provider Configuration{Colors.END}")
    passed, issues = check_providers_config()
    all_issues.extend(issues)
    
    # 3. Check Discord token
    print(f"\n{Colors.BOLD}[3/4] Discord Token{Colors.END}")
    passed, issues = check_discord_token()
    all_issues.extend(issues)
    
    # 4. Check characters
    print(f"\n{Colors.BOLD}[4/4] Characters{Colors.END}")
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
