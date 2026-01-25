"""
Discord Pals - Global Request Coordinator
Manages cross-bot coordination for concurrent AI requests.
"""

import asyncio
import time
from typing import Dict, Set, Optional
from collections import defaultdict
import runtime_config
import logger as log


class GlobalCoordinator:
    """Singleton coordinator for cross-bot request management.

    Prevents crashes when multiple bots are tagged simultaneously by:
    1. Limiting concurrent AI requests via semaphore
    2. Staggering responses when multiple bots respond to the same message
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # Global semaphore for AI request limiting (created lazily)
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._semaphore_limit: int = 0

        # Track which bots are processing which messages
        # message_id -> set of bot_names currently processing
        self._active_requests: Dict[int, Set[str]] = defaultdict(set)

        # Lock created lazily to avoid event loop issues
        self._request_lock: Optional[asyncio.Lock] = None

        # Track response order for staggering
        # message_id -> list of (bot_name, queue_time)
        self._response_queue: Dict[int, list] = defaultdict(list)

        # Cleanup tracking
        self._last_cleanup = time.time()
        self._CLEANUP_INTERVAL = 300  # 5 minutes
        self._STALE_THRESHOLD = 60  # 1 minute

    def _get_lock(self) -> asyncio.Lock:
        """Get or create the request lock (lazy initialization for event loop safety)."""
        if self._request_lock is None:
            self._request_lock = asyncio.Lock()
        return self._request_lock

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Get or create the global semaphore with current limit."""
        limit = runtime_config.get("concurrency_limit", 4)

        # Recreate if limit changed or not yet created
        if self._semaphore is None or self._semaphore_limit != limit:
            self._semaphore = asyncio.Semaphore(limit)
            self._semaphore_limit = limit
            log.info(f"Global coordinator: semaphore set to {limit} concurrent requests")

        return self._semaphore

    async def acquire_slot(self, bot_name: str, message_id: int) -> bool:
        """Acquire a slot for AI request. Blocks if at concurrency limit.

        Args:
            bot_name: Name of the bot requesting a slot
            message_id: Discord message ID being processed

        Returns:
            True when slot is acquired
        """
        try:
            semaphore = self._get_semaphore()
            lock = self._get_lock()

            async with lock:
                # Register this bot as processing this message
                self._active_requests[message_id].add(bot_name)

                # Queue for staggered response
                self._response_queue[message_id].append((bot_name, time.time()))

            # Acquire semaphore (blocks if at limit)
            await semaphore.acquire()
            log.debug(f"[{bot_name}] Acquired AI slot for message {message_id}")
            return True
        except Exception as e:
            log.error(f"[{bot_name}] Failed to acquire slot: {e}")
            return True  # Continue anyway to not block the bot

    def release_slot(self, bot_name: str, message_id: int):
        """Release the AI request slot.

        Args:
            bot_name: Name of the bot releasing the slot
            message_id: Discord message ID that was processed
        """
        try:
            semaphore = self._get_semaphore()
            semaphore.release()

            # Clean up tracking
            if message_id in self._active_requests:
                self._active_requests[message_id].discard(bot_name)
                if not self._active_requests[message_id]:
                    del self._active_requests[message_id]

            log.debug(f"[{bot_name}] Released AI slot for message {message_id}")

            # Periodic cleanup
            self._maybe_cleanup()
        except Exception as e:
            log.error(f"[{bot_name}] Failed to release slot: {e}")

    async def get_stagger_delay(self, bot_name: str, message_id: int) -> float:
        """Get delay before sending response to stagger multi-bot replies.

        Args:
            bot_name: Name of the bot requesting delay
            message_id: Discord message ID being responded to

        Returns:
            Delay in seconds (0.0 if first responder, up to 5.0 for later ones)
        """
        try:
            lock = self._get_lock()
            async with lock:
                queue = self._response_queue.get(message_id, [])

                # Find position in queue
                position = 0
                for i, (name, _) in enumerate(queue):
                    if name == bot_name:
                        position = i
                        break

                # Stagger by 1.5 seconds per position
                delay = position * 1.5
                return min(delay, 5.0)  # Cap at 5 seconds
        except Exception as e:
            log.error(f"[{bot_name}] Failed to get stagger delay: {e}")
            return 0.0

    def get_active_bots_for_message(self, message_id: int) -> Set[str]:
        """Get set of bot names currently processing a message.

        Args:
            message_id: Discord message ID to check

        Returns:
            Set of bot names processing this message
        """
        return self._active_requests.get(message_id, set()).copy()

    def _maybe_cleanup(self):
        """Clean up stale tracking data."""
        now = time.time()
        if now - self._last_cleanup < self._CLEANUP_INTERVAL:
            return

        self._last_cleanup = now

        # Clean stale response queues
        stale_messages = []
        for msg_id, queue in self._response_queue.items():
            if queue and now - queue[0][1] > self._STALE_THRESHOLD:
                stale_messages.append(msg_id)

        for msg_id in stale_messages:
            del self._response_queue[msg_id]
            self._active_requests.pop(msg_id, None)

        if stale_messages:
            log.debug(f"Coordinator cleanup: removed {len(stale_messages)} stale entries")


# Global singleton instance
coordinator = GlobalCoordinator()
