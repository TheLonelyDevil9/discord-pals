"""
Discord Pals - Constants
Centralized configuration constants to avoid magic numbers throughout the codebase.
"""

# =============================================================================
# ANTI-LOOP / RATE LIMITING
# =============================================================================

BOT_CHAIN_COOLDOWN_SECONDS = 60  # Cooldown between bot-to-bot interactions
MAX_RESPONSES_PER_MINUTE = 5     # Max responses per channel per minute
MAX_USER_MESSAGES_PER_MINUTE = 10  # Max messages per user per minute (spam protection)
CIRCUIT_BREAKER_THRESHOLD = 3    # Consecutive failures before circuit breaker trips
DUPLICATE_CHECK_COUNT = 5        # Number of recent responses to check for duplicates

# =============================================================================
# MESSAGE PROCESSING
# =============================================================================

MESSAGE_DELAY_MIN = 0.5          # Minimum delay between message parts (seconds)
MESSAGE_DELAY_MAX = 1.0          # Maximum delay between message parts (seconds)
ERROR_DELETE_AFTER = 10          # Seconds before error messages auto-delete
MAX_MESSAGE_LENGTH = 2000        # Discord's max message length

# =============================================================================
# MEMORY LIMITS
# =============================================================================

MAX_SERVER_MEMORIES = 50         # Max memories per server
MAX_DM_MEMORIES_PER_USER = 30    # Max DM memories per user per character
MAX_USER_MEMORIES_PER_SERVER = 20  # Max user memories per server per character
MAX_GLOBAL_USER_FACTS = 20       # Max facts in global user profile

# =============================================================================
# SEMANTIC DEDUPLICATION
# =============================================================================

SEMANTIC_SIMILARITY_THRESHOLD = 0.85  # Cosine similarity threshold for semantic match
TEXTUAL_SIMILARITY_THRESHOLD = 0.75   # SequenceMatcher ratio for textual match
KEY_TERM_OVERLAP_THRESHOLD = 0.3      # Minimum term overlap to proceed with checks

# =============================================================================
# HISTORY
# =============================================================================

MAX_HISTORY_MESSAGES = 300       # Max messages stored per channel (reduced from 1000 for memory efficiency)
DEFAULT_HISTORY_LIMIT = 200      # Default messages included in context
HISTORY_SAVE_INTERVAL = 60       # Seconds between history saves

# =============================================================================
# CACHING
# =============================================================================

RUNTIME_CONFIG_CACHE_TTL = 30.0  # Seconds before runtime config cache expires
STATS_SAVE_INTERVAL = 30         # Seconds between stats saves
MEMORY_SAVE_INTERVAL = 10        # Seconds between memory saves
STALE_STATE_THRESHOLD = 3600     # Seconds before channel state is considered stale (1 hour)

# =============================================================================
# DASHBOARD
# =============================================================================

DASHBOARD_DEFAULT_HOST = '127.0.0.1'
DASHBOARD_DEFAULT_PORT = 5000

# Allowed files for ZIP import (security whitelist)
ALLOWED_IMPORT_FILES = {'providers.json', 'bots.json', 'autonomous.json'}

# =============================================================================
# USER-FRIENDLY ERROR MESSAGES
# =============================================================================

USER_FRIENDLY_ERRORS = {
    "timeout": "The AI provider is taking too long to respond. Please try again.",
    "rate_limit": "Too many requests. Please wait a moment.",
    "provider_error": "The AI service is temporarily unavailable.",
    "context_too_long": "The conversation is too long. Try using /clear to reset.",
    "invalid_response": "Received an invalid response. Please try again.",
    "default": "Something went wrong. Please try again.",
}
