"""Read-only setup diagnostics, run on the selected helper's Discord loop."""

from __future__ import annotations

import asyncio

from project_automation_config import CHANNEL_PURPOSES, config_errors, credential_status


CHECK_TIMEOUT = 12
OPTIONS_TIMEOUT = 3
PERMISSION_LABELS = {
    "view_channel": "View Channel", "read_message_history": "Read Message History",
    "send_messages": "Send Messages", "embed_links": "Embed Links",
    "send_messages_in_threads": "Send Messages in Threads", "manage_threads": "Manage Threads",
    "create_public_threads": "Create Public Threads", "attach_files": "Attach Files",
}


def check(key, label, status, detail):
    return {"key": key, "label": label, "status": status, "detail": detail}


def _channel_type(channel):
    value = getattr(channel, "type", None)
    return {0: "text", 15: "forum"}.get(getattr(value, "value", value), "unsupported")


def _client(bot):
    return getattr(bot, "client", None)


def _online(client):
    return client is not None and callable(getattr(client, "is_ready", None)) and client.is_ready()


def _guild(client, guild_id):
    # Never look through another bot's topology or resolve a channel globally.
    return next((guild for guild in getattr(client, "guilds", ()) if str(guild.id) == guild_id), None)


def _permissions(channel, member):
    if member is None or not callable(getattr(channel, "permissions_for", None)):
        return None
    return channel.permissions_for(member)


def unavailable_options(config, detail):
    return {"bot_name": config["bot_name"], "guild_id": config["guild_id"], "guilds": [], "channels": [],
            "status": "unverified", "detail": detail}


async def discord_options(config, bot):
    client = _client(bot)
    if not _online(client):
        return unavailable_options(config, "Start the selected helper to load its servers and channels. Saved IDs are preserved.")
    result = unavailable_options(config, "Choose a server to load this helper's channels.")
    result["status"] = "passed"
    result["guilds"] = sorted(
        ({"id": str(guild.id), "name": str(guild.name)} for guild in client.guilds),
        key=lambda item: (item["name"].casefold(), item["id"]),
    )
    guild = _guild(client, config["guild_id"])
    if not guild:
        if config["guild_id"]:
            result.update(status="attention", detail="The selected helper cannot see this server. Check its membership or choose another server.")
        return result
    if getattr(guild, "unavailable", False) or getattr(guild, "me", None) is None:
        result.update(status="unverified", detail="Discord's server details are temporarily unavailable. Keep the saved IDs and retry.")
        return result
    channels = []
    for channel in getattr(guild, "channels", ()):
        kind = _channel_type(channel)
        permissions = _permissions(channel, guild.me)
        if kind not in {"forum", "text"} or getattr(permissions, "view_channel", None) is not True:
            continue
        category = getattr(channel, "category", None)
        channels.append({"id": str(channel.id), "name": str(channel.name), "type": kind,
                         "category": str(category.name) if category else ""})
    result["channels"] = sorted(channels, key=lambda item: (item["category"].casefold(), item["name"].casefold(), item["id"]))
    result["detail"] = "Showing channels visible to the selected helper. Each purpose requires a separate channel."
    return result


def local_checks(config):
    errors = config_errors(config)
    credentials = credential_status(config)
    return [
        check("configuration", "Required settings", "attention" if errors else "passed",
              " ".join(errors) if errors else "The required settings are present and destinations are distinct."),
        check("private_key", "GitHub App private key", "passed" if credentials["private_key"] else "attention",
              "A key source is available; GitHub access is checked below." if credentials["private_key"]
              else "Configure the private key in the named environment variable or its configured file."),
        check("webhook_secret", "Webhook secret", "passed" if credentials["webhook_secret"] else "attention",
              "The named environment variable is set. A signed webhook delivery is still needed to verify the connection."
              if credentials["webhook_secret"] else "Set the webhook secret in the named environment variable and GitHub App settings."),
    ]


def unavailable_checks(detail):
    return [check("discord_helper", "Helper connection", "unverified", detail),
            check("discord_server", "Discord server", "unverified", "Server access cannot be checked until the selected helper is connected."),
            *[check("discord_" + field, label, "unverified", "Channel type and permissions cannot be checked until the selected helper is connected.")
              for field, label, _, _ in CHANNEL_PURPOSES],
            check("github_connection", "GitHub connection", "unverified", "Retry when the selected helper's event loop is running.")]


def discord_checks(config, bot):
    client = _client(bot)
    if not _online(client):
        return unavailable_checks("Start the selected helper, then run Check setup again.")[:-1]
    checks = [check("discord_helper", "Helper connection", "passed", "The selected helper is connected to Discord.")]
    guild = _guild(client, config["guild_id"])
    if guild is None:
        checks.append(check("discord_server", "Discord server", "attention", "Choose a server that the selected helper has joined."))
    elif getattr(guild, "unavailable", False) or getattr(guild, "me", None) is None:
        checks.append(check("discord_server", "Discord server", "unverified", "Discord's server or member details are unavailable. Retry after the connection recovers."))
        guild = None
    else:
        checks.append(check("discord_server", "Discord server", "passed", "The selected helper belongs to this server."))
    for field, label, allowed, _ in CHANNEL_PURPOSES:
        channel_id = config[field]
        key = "discord_" + field
        if not channel_id:
            optional = field == "support_channel_id"
            checks.append(check(key, label, "passed" if optional else "attention",
                                "No support handoff channel is configured; this is optional." if optional else "Choose a channel for this purpose."))
            continue
        if guild is None:
            checks.append(check(key, label, "unverified", "Check server access before checking this channel."))
            continue
        channel = next((item for item in getattr(guild, "channels", ()) if str(item.id) == channel_id), None)
        if channel is None:
            checks.append(check(key, label, "attention", "This channel is unavailable in the selected server. Check its ID, deletion, and the helper's access."))
            continue
        kind = _channel_type(channel)
        if kind not in allowed:
            checks.append(check(key, label, "attention", "Choose a " + " or ".join(allowed) + " channel for this purpose."))
            continue
        permissions = _permissions(channel, guild.me)
        required = ["view_channel", "read_message_history", "send_messages", "embed_links"]
        if kind == "forum" or field == "feedback_channel_id":
            required += ["send_messages_in_threads", "manage_threads"]
        if field == "feedback_channel_id" and kind == "text":
            required.append("create_public_threads")
        if field == "feedback_channel_id":
            required.append("attach_files")
        missing = [name for name in required if getattr(permissions, name, None) is False]
        unknown = [name for name in required if getattr(permissions, name, None) is None]
        if missing:
            checks.append(check(key, label, "attention", "Grant " + ", ".join(PERMISSION_LABELS[name] for name in missing) + " in this channel, including role/channel overrides."))
        elif unknown:
            checks.append(check(key, label, "unverified", "The helper's effective permissions are unavailable. Retry after Discord reconnects."))
        else:
            checks.append(check(key, label, "passed", "The channel type and required permissions are available."))
    intents = getattr(client, "intents", None)
    if getattr(intents, "message_content", None) is False or getattr(intents, "members", None) is False:
        checks.append(check("discord_intents", "Feedback intents", "attention", "Enable Message Content and Server Members intents for the helper and restart it."))
    else:
        checks.append(check("discord_intents", "Feedback intents", "unverified", "Confirm Message Content and Server Members intents in the Discord Developer Portal; cached settings do not verify portal grants."))
    return checks


async def setup_checks(config, bot, github_factory=None):
    checks = discord_checks(config, bot)
    if not config["repository"] or not config["github_app_id"] or not config["github_installation_id"] or not credential_status(config)["private_key"]:
        checks.append(check("github_connection", "GitHub connection", "unverified", "Complete the repository, App IDs, and private-key source before checking GitHub access."))
        return checks
    if github_factory is None:
        from project_automation_github import GitHubClient
        github_factory = GitHubClient
    client = None
    try:
        client = github_factory(config)
        github_checks = await asyncio.wait_for(client.setup_checks(), timeout=CHECK_TIMEOUT - 2)
        checks.extend(row for row in github_checks if row.get("key") != "github_webhook_secret")
    except Exception:
        # Provider responses and exception strings may contain credential/path data.
        checks.append(check("github_connection", "GitHub connection", "unverified", "GitHub checks did not complete. Check connectivity and App credentials, then retry."))
    finally:
        if client is not None:
            await client.close()
    return checks
