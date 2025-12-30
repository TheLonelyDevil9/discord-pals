"""
Discord Pals - Logging Utilities
Clean, organized logging with emoji indicators.
"""

import sys
from datetime import datetime

# Log levels
QUIET = 0   # Only errors
NORMAL = 1  # Errors + important events
VERBOSE = 2 # Everything

# Set this to control verbosity (QUIET=errors only, NORMAL=+events, VERBOSE=+debug)
LOG_LEVEL = QUIET


class Colors:
    """ANSI color codes for terminal output."""
    OK = '\033[92m'      # Green
    WARN = '\033[93m'    # Yellow
    FAIL = '\033[91m'    # Red
    INFO = '\033[94m'    # Blue
    DIM = '\033[90m'     # Gray
    BOLD = '\033[1m'
    END = '\033[0m'


def _timestamp():
    """Get current time as HH:MM:SS."""
    return datetime.now().strftime("%H:%M:%S")


def _log(icon: str, color: str, msg: str, bot_name: str = None, level: int = NORMAL):
    """Internal logging function."""
    if level > LOG_LEVEL:
        return
    
    ts = f"{Colors.DIM}{_timestamp()}{Colors.END}"
    prefix = f"[{bot_name}] " if bot_name else ""
    print(f"{ts} {color}{icon}{Colors.END} {prefix}{msg}")


# Public logging functions
def ok(msg: str, bot: str = None):
    """Log success message."""
    _log("✓", Colors.OK, msg, bot, NORMAL)


def warn(msg: str, bot: str = None):
    """Log warning message."""
    _log("⚠", Colors.WARN, msg, bot, NORMAL)


def error(msg: str, bot: str = None):
    """Log error message."""
    _log("✗", Colors.FAIL, msg, bot, QUIET)


def info(msg: str, bot: str = None):
    """Log info message."""
    _log("ℹ", Colors.INFO, msg, bot, NORMAL)


def debug(msg: str, bot: str = None):
    """Log debug message (only in verbose mode)."""
    _log("•", Colors.DIM, msg, bot, VERBOSE)


def startup(msg: str):
    """Log startup message (always shown)."""
    print(f"{Colors.BOLD}{msg}{Colors.END}")


def online(msg: str, bot: str = None):
    """Log bot online status (always shown)."""
    ts = f"{Colors.DIM}{_timestamp()}{Colors.END}"
    prefix = f"[{bot}] " if bot else ""
    print(f"{ts} {Colors.OK}●{Colors.END} {prefix}{msg}")


def divider():
    """Print a divider line."""
    print(f"{Colors.DIM}{'─' * 50}{Colors.END}")
