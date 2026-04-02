"""
Slash command metadata and visibility helpers.
"""

from __future__ import annotations

from discord import app_commands


def _noop_decorator(*args, **kwargs):
    del args, kwargs

    def decorator(func):
        return func

    return decorator


def maintenance_visibility():
    """Restrict maintenance commands where Discord permissions support it."""
    decorator_factory = getattr(app_commands, "default_permissions", None)
    if callable(decorator_factory):
        return decorator_factory(manage_guild=True)
    return _noop_decorator()


def reset_command_registry(bot_instance) -> None:
    """Reset command metadata for a bot before command registration."""
    bot_instance._command_registry = {}
    bot_instance._command_registry_order = []


def register_command_metadata(
    bot_instance,
    *,
    name: str,
    audience: str,
    description: str = "",
    kind: str = "command",
    subcommands: list[dict] | None = None,
) -> None:
    """Record top-level command metadata for sync status and dashboard inspection."""
    if not hasattr(bot_instance, "_command_registry"):
        reset_command_registry(bot_instance)

    entry = {
        "name": name,
        "audience": audience,
        "description": description,
        "kind": kind,
        "subcommands": list(subcommands or []),
    }

    if name not in bot_instance._command_registry:
        bot_instance._command_registry_order.append(name)
    bot_instance._command_registry[name] = entry


def get_command_inventory(bot_instance) -> list[dict]:
    """Return registered top-level command metadata in registration order."""
    registry = getattr(bot_instance, "_command_registry", {})
    order = getattr(bot_instance, "_command_registry_order", [])
    inventory = []
    for name in order:
        entry = registry.get(name)
        if entry:
            inventory.append({
                "name": entry["name"],
                "audience": entry["audience"],
                "description": entry["description"],
                "kind": entry["kind"],
                "subcommands": [dict(sub) for sub in entry.get("subcommands", [])],
            })
    return inventory
