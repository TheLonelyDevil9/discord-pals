"""Safe dotenv helpers for dashboard-managed process secrets."""

import os
import stat
from pathlib import Path


ENV_FILE = Path(".env")
DISCORD_TOKEN_ENV_KEY = "DISCORD_TOKEN"


def extract_env_line_key(line: str) -> str | None:
    """Return the key from a dotenv assignment line, ignoring comments."""
    stripped = line.lstrip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    return stripped.split("=", 1)[0].strip()


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


def discord_token_status() -> dict:
    """Return dashboard-safe Discord token metadata."""
    token_value = read_env_value(DISCORD_TOKEN_ENV_KEY)
    configured = bool(token_value) and token_value != "your_discord_bot_token_here"
    return {
        "key": DISCORD_TOKEN_ENV_KEY,
        "configured": configured,
        "env_file_exists": ENV_FILE.exists(),
        "single_bot_mode": not Path("bots.json").exists(),
        "restart_required": False,
    }
