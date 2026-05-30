"""
Discord Pals - Global Request Coordinator
Manages cross-bot coordination for concurrent AI requests.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Deque, Dict, Optional, Set, Tuple, Union
from collections import defaultdict, deque
import runtime_config
import logger as log


@dataclass
class CoordinatorSlot:
    """Release-once token for a coordinator capacity slot."""

    bot_name: str
    message_id: int
    token_id: int
    released: bool = False


class GlobalCoordinator:
    """Singleton coordinator for cross-bot request management.

    Prevents crashes when multiple bots are tagged simultaneously by:
    1. Limiting concurrent AI requests via release-once slot tokens
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

        # Global capacity tracking for AI request limiting.
        self._slot_limit: int = 0
        self._active_slots: Dict[int, CoordinatorSlot] = {}
        self._legacy_slot_ids: Dict[Tuple[str, int], Deque[int]] = defaultdict(deque)
        self._next_slot_id: int = 0
        self._slot_event: Optional[asyncio.Event] = None
        self._RESIZE_POLL_INTERVAL = 0.05

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

    def _get_slot_event(self) -> asyncio.Event:
        """Get or create the slot wake event (lazy initialization for event loop safety)."""
        if self._slot_event is None:
            self._slot_event = asyncio.Event()
        return self._slot_event

    def _configured_limit(self) -> int:
        """Read and normalize the configured concurrency limit."""
        try:
            limit = int(runtime_config.get("concurrency_limit", 4))
        except (TypeError, ValueError):
            limit = 4
        return max(1, limit)

    def _refresh_slot_limit(self) -> int:
        """Refresh the capacity limit without replacing active slot state."""
        limit = self._configured_limit()
        previous_limit = self._slot_limit
        if previous_limit != limit:
            self._slot_limit = limit
            log.info(f"Global coordinator: capacity set to {limit} concurrent requests")
            if previous_limit and limit > previous_limit and self._slot_event is not None:
                self._slot_event.set()
        return self._slot_limit

    def _issue_slot(self, bot_name: str, message_id: int) -> CoordinatorSlot:
        """Create and track a release-once slot token."""
        self._next_slot_id += 1
        token = CoordinatorSlot(bot_name=bot_name, message_id=message_id, token_id=self._next_slot_id)
        self._active_slots[token.token_id] = token
        self._legacy_slot_ids[(bot_name, message_id)].append(token.token_id)
        self._active_requests[message_id].add(bot_name)
        return token

    def _resolve_release_token(
        self,
        slot_or_bot_name: Union[CoordinatorSlot, str],
        message_id: Optional[int],
    ) -> Optional[CoordinatorSlot]:
        """Resolve either the new token path or legacy bot/message release path."""
        if isinstance(slot_or_bot_name, CoordinatorSlot):
            token = self._active_slots.get(slot_or_bot_name.token_id)
            if token is slot_or_bot_name and not token.released:
                return token
            return None

        if message_id is None:
            return None

        key = (slot_or_bot_name, message_id)
        token_ids = self._legacy_slot_ids.get(key)
        while token_ids:
            token_id = token_ids.popleft()
            token = self._active_slots.get(token_id)
            if token is not None and not token.released:
                return token

        self._legacy_slot_ids.pop(key, None)
        return None

    def _forget_legacy_slot(self, token: CoordinatorSlot):
        """Remove a token ID from the legacy release lookup."""
        key = (token.bot_name, token.message_id)
        token_ids = self._legacy_slot_ids.get(key)
        if not token_ids:
            return

        remaining = deque(
            token_id
            for token_id in token_ids
            if token_id != token.token_id and token_id in self._active_slots
        )
        if remaining:
            self._legacy_slot_ids[key] = remaining
        else:
            self._legacy_slot_ids.pop(key, None)

    async def acquire_slot(self, bot_name: str, message_id: int) -> CoordinatorSlot:
        """Acquire a slot for AI request. Blocks if at concurrency limit.

        Args:
            bot_name: Name of the bot requesting a slot
            message_id: Discord message ID being processed

        Returns:
            CoordinatorSlot token to pass to release_slot()
        """
        try:
            # Queue for staggered response when the request enters the coordinator.
            self._response_queue[message_id].append((bot_name, time.time()))

            while True:
                limit = self._refresh_slot_limit()
                if len(self._active_slots) < limit:
                    token = self._issue_slot(bot_name, message_id)
                    log.debug(f"[{bot_name}] Acquired AI slot for message {message_id}")
                    return token

                slot_event = self._get_slot_event()
                slot_event.clear()
                try:
                    await asyncio.wait_for(slot_event.wait(), timeout=self._RESIZE_POLL_INTERVAL)
                except asyncio.TimeoutError:
                    pass
        except Exception as e:
            log.error(f"[{bot_name}] Failed to acquire slot: {e}")
            # Continue anyway to not block the bot; releasing this token is a no-op.
            return CoordinatorSlot(bot_name=bot_name, message_id=message_id, token_id=-1, released=True)

    def release_slot(
        self,
        slot_or_bot_name: Union[CoordinatorSlot, str],
        message_id: Optional[int] = None,
    ):
        """Release the AI request slot.

        Args:
            slot_or_bot_name: CoordinatorSlot token, or legacy bot name
            message_id: Legacy Discord message ID that was processed
        """
        try:
            token = self._resolve_release_token(slot_or_bot_name, message_id)
            if token is None:
                return

            token.released = True
            self._active_slots.pop(token.token_id, None)
            self._forget_legacy_slot(token)

            # Clean up tracking
            if token.message_id in self._active_requests:
                self._active_requests[token.message_id].discard(token.bot_name)
                if not self._active_requests[token.message_id]:
                    del self._active_requests[token.message_id]

            log.debug(f"[{token.bot_name}] Released AI slot for message {token.message_id}")

            if self._slot_event is not None:
                self._slot_event.set()

            # Periodic cleanup
            self._maybe_cleanup()
        except Exception as e:
            bot_name = getattr(slot_or_bot_name, "bot_name", slot_or_bot_name)
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
            if not any(slot.message_id == msg_id for slot in self._active_slots.values()):
                self._active_requests.pop(msg_id, None)

        if stale_messages:
            log.debug(f"Coordinator cleanup: removed {len(stale_messages)} stale entries")


# Global singleton instance
coordinator = GlobalCoordinator()
