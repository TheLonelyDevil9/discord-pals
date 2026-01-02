"""
Discord Pals - Commands Package
Organizes slash commands into logical groups.
"""

# Re-export command registration functions for easy import
from .core import setup_core_commands
from .memory import setup_memory_commands
from .fun import setup_fun_commands


def setup_all_commands(bot_instance):
    """Register all commands for a bot instance."""
    setup_core_commands(bot_instance)
    setup_memory_commands(bot_instance)
    setup_fun_commands(bot_instance)
