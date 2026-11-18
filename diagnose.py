#!/usr/bin/env python3
"""
Discord Pals - Provider Diagnostics
Run this script to diagnose provider connectivity issues.
"""

import os
import sys
import json
from pathlib import Path

import dashboard_provider_health

# Colors for terminal output
class Colors:
    OK = '\033[92m'
    WARN = '\033[93m'
    FAIL = '\033[91m'
    INFO = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'

def ok(msg): print(f"{Colors.OK}✓ {msg}{Colors.END}")
def warn(msg): print(f"{Colors.WARN}⚠ {msg}{Colors.END}")
def fail(msg): print(f"{Colors.FAIL}✗ {msg}{Colors.END}")
def info(msg): print(f"{Colors.INFO}ℹ {msg}{Colors.END}")
def header(msg): print(f"\n{Colors.BOLD}{'='*50}\n{msg}\n{'='*50}{Colors.END}")


def check_files():
    """Check required configuration files."""
    header("1. Checking Configuration Files")
    
    base_dir = Path(__file__).parent
    issues = []
    
    # Check .env
    env_file = base_dir / ".env"
    env_example = base_dir / ".env.example"
    if env_file.exists():
        ok(f".env file found")
        # Check for required vars
        with open(env_file) as f:
            content = f.read()
            if "DISCORD_TOKEN" in content:
                ok("  DISCORD_TOKEN is set")
            else:
                warn("  DISCORD_TOKEN not found in .env")
    else:
        fail(".env file NOT FOUND")
        if env_example.exists():
            info("  → Copy .env.example to .env and fill in your values")
        issues.append("missing .env")
    
    # Check providers.json
    providers_file = base_dir / "providers.json"
    providers_example = base_dir / "providers.json.example"
    if providers_file.exists():
        ok("providers.json found")
        try:
            with open(providers_file) as f:
                data = json.load(f)
            providers = data.get("providers", [])
            ok(f"  {len(providers)} provider(s) configured")
            for i, p in enumerate(providers):
                info(f"  [{i+1}] {p.get('name', 'Unnamed')} → {p.get('url', 'NO URL')}")
        except json.JSONDecodeError as e:
            fail(f"  Invalid JSON: {e}")
            issues.append("invalid providers.json")
    else:
        fail("providers.json NOT FOUND")
        if providers_example.exists():
            info("  → Copy providers.json.example to providers.json and configure")
        issues.append("missing providers.json")
    
    return issues


def check_providers(base_dir=None):
    """Run the dashboard's Test model check on each saved provider and return one issue per failure.

    Key variables come from .env, which config.py loads when the provider test first imports it,
    the same way the bot reads them.
    """
    header("2. Testing Providers")

    base_dir = Path(base_dir) if base_dir else Path(__file__).parent
    try:
        data = json.loads((base_dir / "providers.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        fail("Cannot test - providers.json is missing or not valid JSON")
        return ["providers not tested"]
    if not isinstance(data, dict) or not isinstance(data.get("providers"), list):
        fail("Cannot test - providers.json has no providers list")
        return ["providers not tested"]
    if not data["providers"]:
        warn("providers.json lists no providers")

    issues = []
    for index, provider in enumerate(data["providers"]):
        entry = provider if isinstance(provider, dict) else {}
        name = entry.get("name") or f"Provider {index + 1}"
        print(f"\n{Colors.BOLD}Testing: {name}{Colors.END}")

        result = dashboard_provider_health.test_provider_connection(
            provider, index=index, default_timeout=data.get("timeout", 60))
        for check in result["checks"]:
            report = {"passed": ok, "failed": fail}.get(check["status"], info)
            report(f"  {check['label']}: {check['detail']}")
        if result["success"]:
            continue

        issues.append(f"{name}: {result['error']}")
        key_env = entry.get("key_env")
        settings_failed = result["checks"][0]["status"] == "failed"
        if settings_failed and isinstance(key_env, str) and key_env and not entry.get("api_key") \
                and not os.getenv(key_env):
            info(f"  {key_env} is not set in .env or the process environment")

    return issues


def main():
    print(f"\n{Colors.BOLD}Discord Pals - Provider Diagnostics{Colors.END}")
    print(f"Running from: {Path(__file__).parent}\n")
    
    all_issues = []
    
    # Check files
    all_issues.extend(check_files())
    
    try:
        all_issues.extend(check_providers())
    except ImportError as e:
        fail(f"A dependency is missing ({e.name}). Run: pip install -r requirements.lock")
        all_issues.append(f"missing {e.name}")
    
    # Summary
    header("SUMMARY")
    if all_issues:
        fail(f"Found {len(all_issues)} issue(s):")
        for issue in all_issues:
            print(f"  • {issue}")
    else:
        ok("All checks passed!")
    
    print()


if __name__ == "__main__":
    main()
