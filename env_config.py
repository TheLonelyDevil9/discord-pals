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


def load_bots_json_data(bots_file: Path | None = None) -> tuple[dict, str | None]:
    """Load bots.json for dashboard-managed bot-mode controls."""
    bots_file = bots_file or BOTS_FILE
    try:
        data = json.loads(bots_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"bots": []}, None
    except json.JSONDecodeError as e:
        return {"bots": []}, f"Invalid bots.json: {e}"
    except OSError as e:
        return {"bots": []}, f"Could not read bots.json: {e}"

    if not isinstance(data, dict):
        return {"bots": []}, "bots.json must contain an object"

    bots = data.get("bots")
    if bots is None:
        data["bots"] = []
    elif not isinstance(bots, list):
        return {"bots": []}, 'bots.json "bots" must be a list'
    return data, None


def load_bot_mode_config(bots_file: Path | None = None) -> dict:
    """Return dashboard-safe bot-mode state without exposing token values."""
    bots_file = bots_file or BOTS_FILE
    data, error = load_bots_json_data(bots_file)
    mode = "multi" if bots_file.exists() else "single"
    bots = []
    if isinstance(data.get("bots"), list):
        for index, bot_cfg in enumerate(data["bots"], start=1):
            if not isinstance(bot_cfg, dict):
                continue
            bots.append({
                "name": str(bot_cfg.get("name") or f"Bot {index}").strip(),
                "character": str(bot_cfg.get("character") or "").strip(),
                "token_env": str(bot_cfg.get("token_env") or "").strip(),
                "nicknames": str(bot_cfg.get("nicknames") or "").strip(),
            })

    return {
        "mode": mode,
        "single_bot_mode": mode == "single",
        "multi_bot_mode": mode == "multi",
        "bots": bots,
        "error": error,
        "restart_required": False,
    }


def _sanitize_bot_name(value, index: int) -> str | None:
    """Normalize a dashboard bot display name."""
    del index
    name = str(value or "").strip()
    if not name:
        return None
    return re.sub(r"\s+", " ", name)[:80]


def normalize_bot_mode_payload(
    data: dict,
    available_characters: list[str] | set[str] | tuple[str, ...] | None = None,
) -> tuple[dict | None, str | None]:
    """Validate dashboard bot-mode payload before writing bots.json."""
    if not isinstance(data, dict):
        return None, "JSON object required"

    mode = str(data.get("mode") or "").strip().lower()
    if mode not in {"single", "multi"}:
        return None, "Choose single or multi bot mode"

    if mode == "single":
        return {"mode": "single", "bots": []}, None

    raw_bots = data.get("bots")
    if not isinstance(raw_bots, list):
        return None, "Multi-bot mode needs at least one bot"

    available = set(available_characters or [])
    bots = []
    seen_names = set()
    seen_token_envs = set()
    for index, raw_bot in enumerate(raw_bots, start=1):
        if not isinstance(raw_bot, dict):
            return None, f"Bot {index} is invalid"

        name = _sanitize_bot_name(raw_bot.get("name"), index)
        if not name:
            return None, f"Bot {index} needs a name"
        name_key = name.casefold()
        if name_key in seen_names:
            return None, f"Bot name '{name}' is duplicated"
        seen_names.add(name_key)

        character = str(raw_bot.get("character") or "").strip()
        if not character:
            return None, f"{name} needs a character"
        if available and character not in available:
            return None, f"{name} uses unknown character '{character}'"

        token_env = str(raw_bot.get("token_env") or "").strip()
        if not token_env:
            return None, f"{name} needs a token env"
        if not is_valid_env_key(token_env):
            return None, f"{name} token env is invalid"
        if token_env in seen_token_envs:
            return None, f"Token env '{token_env}' is duplicated"
        seen_token_envs.add(token_env)

        bot = {
            "name": name,
            "token_env": token_env,
            "character": character,
        }
        nicknames = str(raw_bot.get("nicknames") or "").strip()
        if nicknames:
            bot["nicknames"] = nicknames[:240]
        bots.append(bot)

    if not bots:
        return None, "Multi-bot mode needs at least one bot"
    return {"mode": "multi", "bots": bots}, None


def write_bots_json_payload(payload: dict, bots_file: Path | None = None) -> None:
    """Persist validated multi-bot config with stable formatting."""
    bots_file = bots_file or BOTS_FILE
    bots_file.parent.mkdir(parents=True, exist_ok=True)
    bots_file.write_text(
        json.dumps({"bots": payload["bots"]}, indent=2) + "\n",
        encoding="utf-8",
    )


def save_bot_mode(
    data: dict,
    available_characters: list[str] | set[str] | tuple[str, ...] | None = None,
) -> tuple[dict | None, str | None]:
    """Apply a validated single/multi-bot dashboard mode change."""
    payload, error = normalize_bot_mode_payload(data, available_characters)
    if error:
        return None, error

    try:
        if payload["mode"] == "single":
            if BOTS_FILE.exists():
                BOTS_FILE.unlink()
        else:
            write_bots_json_payload(payload)
    except OSError as e:
        return None, f"Save failed: {e}"

    mode_config = load_bot_mode_config()
    mode_config["restart_required"] = True
    mode_config["saved_mode"] = payload["mode"]
    return mode_config, None


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
