"""Safe dotenv helpers for dashboard-managed process secrets."""

import json
import os
import re
import stat
from pathlib import Path


ENV_FILE = Path(".env")
BOTS_FILE = Path("bots.json")
DISCORD_TOKEN_ENV_KEY = "DISCORD_TOKEN"
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PLACEHOLDER_SECRET_VALUES = {
    "",
    "your_discord_bot_token_here",
    "your_discord_token_here",
    "your_token_here",
    "your_bot_token_here",
}


def extract_env_line_key(line: str) -> str | None:
    """Return the key from a dotenv assignment line, ignoring comments."""
    stripped = line.lstrip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    return stripped.split("=", 1)[0].strip()


def is_valid_env_key(key: str) -> bool:
    """Return whether a key is safe to write as a dotenv variable name."""
    return bool(ENV_KEY_RE.fullmatch(key))


def is_configured_secret(value: str | None) -> bool:
    """Return whether a secret value is present and not a known placeholder."""
    if value is None:
        return False
    return value.strip() not in PLACEHOLDER_SECRET_VALUES


def read_env_value(key: str, env_file: Path | None = None) -> str | None:
    """Read one dotenv value without exposing unrelated secrets."""
    env_file = env_file or ENV_FILE
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for line in lines:
        if extract_env_line_key(line) == key:
            return line.split("=", 1)[1].strip()
    return None


def write_env_value(key: str, value: str, env_file: Path | None = None) -> None:
    """Update or append one dotenv value while preserving the rest of the file."""
    env_file = env_file or ENV_FILE
    if not is_valid_env_key(key):
        raise ValueError("Invalid environment variable name")
    if "\n" in value or "\r" in value:
        raise ValueError("Secret values cannot contain line breaks")

    try:
        lines = env_file.read_text(encoding="utf-8").splitlines(keepends=True)
        original_mode = stat.S_IMODE(env_file.stat().st_mode)
    except OSError:
        lines = []
        original_mode = 0o600

    updated = False
    for index, line in enumerate(lines):
        if extract_env_line_key(line) != key:
            continue
        stripped = line.lstrip()
        indent = line[:len(line) - len(stripped)]
        newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        lines[index] = f"{indent}{key}={value}{newline}"
        updated = True
        break

    if not updated:
        if lines and not lines[-1].endswith(("\n", "\r\n")):
            lines[-1] += "\n"
        lines.append(f"{key}={value}\n")

    env_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file = env_file.with_name(env_file.name + ".tmp")
    temp_file.write_text("".join(lines), encoding="utf-8")
    os.chmod(temp_file, original_mode)
    os.replace(temp_file, env_file)


def load_bot_token_targets(bots_file: Path | None = None) -> list[dict]:
    """Return dashboard-safe token targets declared by bots.json."""
    bots_file = bots_file or BOTS_FILE
    try:
        data = json.loads(bots_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(data, dict) or not isinstance(data.get("bots"), list):
        return []

    targets = []
    seen_keys = set()
    for index, bot_cfg in enumerate(data["bots"], start=1):
        if not isinstance(bot_cfg, dict):
            continue
        token_env = str(bot_cfg.get("token_env") or "").strip()
        if not token_env or not is_valid_env_key(token_env) or token_env in seen_keys:
            continue
        seen_keys.add(token_env)
        name = str(bot_cfg.get("name") or f"Bot {index}").strip() or f"Bot {index}"
        character = str(bot_cfg.get("character") or "").strip()
        targets.append({
            "name": name,
            "token_env": token_env,
            "character": character,
            "configured": is_configured_secret(read_env_value(token_env)),
        })
    return targets


def is_declared_bot_token_env(key: str) -> bool:
    """Return whether bots.json declares this token environment variable."""
    return any(target["token_env"] == key for target in load_bot_token_targets())


def discord_token_status() -> dict:
    """Return dashboard-safe Discord token metadata."""
    token_value = read_env_value(DISCORD_TOKEN_ENV_KEY)
    multi_bot_mode = BOTS_FILE.exists()
    return {
        "key": DISCORD_TOKEN_ENV_KEY,
        "configured": is_configured_secret(token_value),
        "env_file_exists": ENV_FILE.exists(),
        "single_bot_mode": not multi_bot_mode,
        "multi_bot_mode": multi_bot_mode,
        "multi_bot_tokens": load_bot_token_targets(),
        "restart_required": False,
    }
