#!/usr/bin/env python3
"""
Discord Pals - Provider Diagnostics
Run this script to diagnose provider connectivity issues.
"""

import os
import sys
import json
import asyncio
from pathlib import Path

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


def check_env_vars():
    """Check environment variables."""
    header("2. Checking Environment Variables")
    
    from dotenv import load_dotenv
    load_dotenv()
    
    # Load providers to see which env vars we need
    providers_file = Path(__file__).parent / "providers.json"
    required_keys = []
    
    if providers_file.exists():
        with open(providers_file) as f:
            data = json.load(f)
        for p in data.get("providers", []):
            key_env = p.get("key_env")
            if key_env:
                required_keys.append((key_env, p.get("name", "Unknown")))
    
    issues = []
    for key_env, provider_name in required_keys:
        value = os.getenv(key_env)
        if value:
            ok(f"{key_env} is set (for {provider_name})")
        else:
            warn(f"{key_env} is NOT SET (for {provider_name})")
            issues.append(f"missing {key_env}")
    
    if not required_keys:
        warn("No providers configured, can't check env vars")
    
    return issues


async def check_connectivity():
    """Test connectivity to each provider."""
    header("3. Testing Provider Connectivity")
    
    providers_file = Path(__file__).parent / "providers.json"
    if not providers_file.exists():
        fail("Cannot test - providers.json not found")
        return ["no providers.json"]
    
    with open(providers_file) as f:
        data = json.load(f)
    
    from dotenv import load_dotenv
    load_dotenv()
    
    issues = []
    
    for p in data.get("providers", []):
        name = p.get("name", "Unknown")
        url = p.get("url", "")
        model = p.get("model", "unknown")
        key_env = p.get("key_env", "")
        api_key = os.getenv(key_env, "")
        
        print(f"\n{Colors.BOLD}Testing: {name}{Colors.END}")
        info(f"  URL: {url}")
        info(f"  Model: {model}")
        
        if not url:
            fail("  No URL configured")
            issues.append(f"{name}: no URL")
            continue
        
        # Test /models endpoint first (basic connectivity)
        import httpx
        
        try:
            models_url = url.rstrip('/') + '/models'
            info(f"  Checking {models_url}...")
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                headers = {}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                
                resp = await client.get(models_url, headers=headers)
                
                if resp.status_code == 200:
                    ok(f"  /models endpoint responded (HTTP 200)")
                    try:
                        models_data = resp.json()
                        if "data" in models_data:
                            available_models = [m.get("id", "?") for m in models_data["data"]]
                            info(f"  Available models: {available_models[:5]}{'...' if len(available_models) > 5 else ''}")
                            if model not in available_models and model != "local-model":
                                warn(f"  Configured model '{model}' not in available models!")
                    except:
                        pass
                else:
                    warn(f"  /models returned HTTP {resp.status_code}")
                    info(f"  Response: {resp.text[:200]}")
                    
        except httpx.ConnectError as e:
            fail(f"  CONNECTION FAILED: Cannot reach {url}")
            fail(f"  Error: {e}")
            issues.append(f"{name}: connection failed")
            info("  → Check if the server is running")
            info("  → Check firewall settings")
            info("  → Verify the URL is correct")
        except httpx.TimeoutException:
            fail(f"  TIMEOUT: Server didn't respond in 10s")
            issues.append(f"{name}: timeout")
        except Exception as e:
            fail(f"  ERROR: {type(e).__name__}: {e}")
            issues.append(f"{name}: {type(e).__name__}")
    
    return issues


async def test_chat_completion():
    """Try an actual chat completion."""
    header("4. Testing Chat Completion")
    
    providers_file = Path(__file__).parent / "providers.json"
    if not providers_file.exists():
        fail("Cannot test - providers.json not found")
        return
    
    with open(providers_file) as f:
        data = json.load(f)
    
    from dotenv import load_dotenv
    load_dotenv()
    
    from openai import AsyncOpenAI
    
    for p in data.get("providers", []):
        name = p.get("name", "Unknown")
        url = p.get("url", "")
        model = p.get("model", "unknown")
        key_env = p.get("key_env", "")
        api_key = os.getenv(key_env, "") or "not-needed"
        
        print(f"\n{Colors.BOLD}Chat test: {name}{Colors.END}")
        
        try:
            client = AsyncOpenAI(
                base_url=url,
                api_key=api_key,
                timeout=30.0
            )
            
            info("  Sending test message...")
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": "Say 'test successful' and nothing else."}
                    ],
                    max_tokens=20,
                    temperature=0
                ),
                timeout=30.0
            )
            
            content = response.choices[0].message.content
            ok(f"  Response received: {content[:100]}")
            
        except asyncio.TimeoutError:
            fail(f"  TIMEOUT after 30s")
        except Exception as e:
            fail(f"  ERROR: {type(e).__name__}: {e}")


async def main():
    print(f"\n{Colors.BOLD}Discord Pals - Provider Diagnostics{Colors.END}")
    print(f"Running from: {Path(__file__).parent}\n")
    
    all_issues = []
    
    # Check files
    all_issues.extend(check_files())
    
    # Check env vars
    try:
        all_issues.extend(check_env_vars())
    except ImportError:
        fail("python-dotenv not installed. Run: pip install python-dotenv")
        all_issues.append("missing python-dotenv")
    
    # Check connectivity
    try:
        all_issues.extend(await check_connectivity())
    except ImportError:
        fail("httpx not installed. Run: pip install httpx")
        all_issues.append("missing httpx")
    
    # Test actual completion
    try:
        await test_chat_completion()
    except Exception as e:
        fail(f"Chat completion test failed: {e}")
    
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
    asyncio.run(main())
