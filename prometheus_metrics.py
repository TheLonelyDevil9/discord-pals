"""
Discord Pals - Prometheus Metrics
Provides Prometheus-compatible metrics for monitoring and observability.
"""

from prometheus_client import Counter, Histogram, Gauge, Info, start_http_server
import time
from typing import Optional
import logger as log


# --- Request Metrics ---

# Total number of messages processed
messages_processed = Counter(
    'discord_pals_messages_processed_total',
    'Total number of messages processed',
    ['bot_name', 'channel_type']  # channel_type: dm, server
)

# Total number of responses generated
responses_generated = Counter(
    'discord_pals_responses_generated_total',
    'Total number of responses generated',
    ['bot_name', 'success']
)

# Response time histogram
response_time = Histogram(
    'discord_pals_response_duration_seconds',
    'Response generation time in seconds',
    ['bot_name', 'provider_tier'],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0]
)

# API request metrics
api_requests = Counter(
    'discord_pals_api_requests_total',
    'Total number of API requests made',
    ['provider_tier', 'status']  # status: success, error, timeout
)

api_request_duration = Histogram(
    'discord_pals_api_request_duration_seconds',
    'API request duration in seconds',
    ['provider_tier'],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0]
)


# --- Memory & State Metrics ---

# Current memory usage (number of channels/users tracked)
active_channels = Gauge(
    'discord_pals_active_channels',
    'Number of active channels being tracked',
    ['bot_name']
)

active_users = Gauge(
    'discord_pals_active_users',
    'Number of active users being tracked',
    ['bot_name']
)

# Message queue depth
queue_depth = Gauge(
    'discord_pals_queue_depth',
    'Current depth of the request queue',
    ['bot_name']
)

# Memory cache sizes
memory_cache_size = Gauge(
    'discord_pals_memory_cache_size',
    'Size of memory caches',
    ['cache_type']  # cache_type: dm, user, server, lore, global_profile
)


# --- Error Metrics ---

# Error counter
errors_total = Counter(
    'discord_pals_errors_total',
    'Total number of errors',
    ['bot_name', 'error_type']  # error_type: api_error, discord_error, timeout, etc.
)

# Circuit breaker trips
circuit_breaker_trips = Counter(
    'discord_pals_circuit_breaker_trips_total',
    'Number of times circuit breaker was triggered',
    ['bot_name', 'channel_id']
)

# Rate limit hits
rate_limit_hits = Counter(
    'discord_pals_rate_limit_hits_total',
    'Number of rate limit hits',
    ['bot_name', 'limit_type']  # limit_type: channel, user
)


# --- Bot Status Metrics ---

# Bot online status
bot_status = Info(
    'discord_pals_bot_info',
    'Bot information and status',
    ['bot_name']
)

# Last activity timestamp
last_activity = Gauge(
    'discord_pals_last_activity_timestamp',
    'Unix timestamp of last activity',
    ['bot_name']
)


# --- Memory Generation Metrics ---

memory_generations = Counter(
    'discord_pals_memory_generations_total',
    'Total number of memory generations',
    ['bot_name', 'memory_type', 'success']  # memory_type: server, user, dm, global
)

memory_generation_duration = Histogram(
    'discord_pals_memory_generation_duration_seconds',
    'Memory generation time in seconds',
    ['bot_name'],
    buckets=[1.0, 2.0, 5.0, 10.0, 20.0]
)


# --- Disk I/O Metrics ---

history_saves = Counter(
    'discord_pals_history_saves_total',
    'Total number of history saves to disk',
    ['save_type']  # save_type: periodic, forced, flush
)

history_save_duration = Histogram(
    'discord_pals_history_save_duration_seconds',
    'History save duration in seconds',
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0]
)

memory_file_saves = Counter(
    'discord_pals_memory_file_saves_total',
    'Total number of memory file saves',
    ['file_type']  # file_type: server, lore, dm, user, global_profile
)


# --- Metrics Manager ---

class MetricsManager:
    """Centralized metrics management for Discord Pals."""

    def __init__(self, metrics_port: int = 8000):
        self.metrics_port = metrics_port
        self._started = False

    def start_metrics_server(self):
        """Start the Prometheus metrics HTTP server."""
        if self._started:
            return

        try:
            start_http_server(self.metrics_port)
            self._started = True
            log.info(f"Prometheus metrics server started on port {self.metrics_port}")
        except Exception as e:
            log.error(f"Failed to start metrics server: {e}")

    # --- Message Metrics ---

    def record_message(self, bot_name: str, channel_type: str = 'server'):
        """Record a processed message."""
        messages_processed.labels(bot_name=bot_name, channel_type=channel_type).inc()

    def record_response(self, bot_name: str, success: bool, duration_seconds: float, provider_tier: str = 'default'):
        """Record a generated response."""
        responses_generated.labels(bot_name=bot_name, success=str(success)).inc()
        response_time.labels(bot_name=bot_name, provider_tier=provider_tier).observe(duration_seconds)

    # --- API Metrics ---

    def record_api_request(self, provider_tier: str, status: str, duration_seconds: float):
        """Record an API request."""
        api_requests.labels(provider_tier=provider_tier, status=status).inc()
        api_request_duration.labels(provider_tier=provider_tier).observe(duration_seconds)

    # --- State Metrics ---

    def update_active_channels(self, bot_name: str, count: int):
        """Update the active channels gauge."""
        active_channels.labels(bot_name=bot_name).set(count)

    def update_active_users(self, bot_name: str, count: int):
        """Update the active users gauge."""
        active_users.labels(bot_name=bot_name).set(count)

    def update_queue_depth(self, bot_name: str, depth: int):
        """Update the queue depth gauge."""
        queue_depth.labels(bot_name=bot_name).set(depth)

    def update_memory_cache_size(self, cache_type: str, size: int):
        """Update memory cache size gauge."""
        memory_cache_size.labels(cache_type=cache_type).set(size)

    # --- Error Metrics ---

    def record_error(self, bot_name: str, error_type: str):
        """Record an error."""
        errors_total.labels(bot_name=bot_name, error_type=error_type).inc()

    def record_circuit_breaker_trip(self, bot_name: str, channel_id: int):
        """Record a circuit breaker trip."""
        circuit_breaker_trips.labels(bot_name=bot_name, channel_id=str(channel_id)).inc()

    def record_rate_limit_hit(self, bot_name: str, limit_type: str):
        """Record a rate limit hit."""
        rate_limit_hits.labels(bot_name=bot_name, limit_type=limit_type).inc()

    # --- Bot Status Metrics ---

    def update_bot_status(self, bot_name: str, character_name: str, online: bool):
        """Update bot status info."""
        bot_status.labels(bot_name=bot_name).info({
            'character': character_name,
            'online': str(online)
        })

    def update_last_activity(self, bot_name: str, timestamp: float):
        """Update last activity timestamp."""
        last_activity.labels(bot_name=bot_name).set(timestamp)

    # --- Memory Metrics ---

    def record_memory_generation(self, bot_name: str, memory_type: str, success: bool, duration_seconds: float):
        """Record a memory generation."""
        memory_generations.labels(
            bot_name=bot_name,
            memory_type=memory_type,
            success=str(success)
        ).inc()
        memory_generation_duration.labels(bot_name=bot_name).observe(duration_seconds)

    # --- Disk I/O Metrics ---

    def record_history_save(self, save_type: str, duration_seconds: Optional[float] = None):
        """Record a history save."""
        history_saves.labels(save_type=save_type).inc()
        if duration_seconds is not None:
            history_save_duration.observe(duration_seconds)

    def record_memory_file_save(self, file_type: str):
        """Record a memory file save."""
        memory_file_saves.labels(file_type=file_type).inc()


# Global metrics manager instance
metrics_manager = MetricsManager()
