"""
Discord Pals - Request Queue
Manages queued requests to avoid spam.
"""

import asyncio
import time
from typing import Dict, List, Callable
from collections import defaultdict, deque
import discord
import logger as log
from request_envelope import RequestEnvelope
from scopes import LocalScopeId, ScopeKey


class RequestQueue:
    """Manages queued requests with safe-locking to prevent spam responses."""
    
    def __init__(self):
        self.queues = defaultdict(deque)
        self.processing: Dict[LocalScopeId, bool] = defaultdict(bool)
        self.locks: Dict[LocalScopeId, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.pending_counts = defaultdict(lambda: defaultdict(int))
        self.pending_signatures = defaultdict(lambda: defaultdict(lambda: defaultdict(deque)))
        self._next_request_id: Dict[LocalScopeId, int] = defaultdict(int)
        self.process_callback: Callable = None

    @staticmethod
    def _split_target_id(split_reply_target) -> int | None:
        """Extract a stable identifier for split-reply duplicate detection."""
        if split_reply_target is None:
            return None
        return getattr(split_reply_target, "id", None)

    @staticmethod
    def _prune_signature_timestamps(signature_timestamps: deque, current_time: float):
        """Remove timestamps that have aged out of the duplicate-detection window."""
        while signature_timestamps and (current_time - signature_timestamps[0]) >= 3:
            signature_timestamps.popleft()

    def _release_request_tracking(self, channel_id: LocalScopeId, request: dict):
        """Release per-user counters and prune duplicate signatures after processing."""
        user_id = request.get("user_id")
        if user_id is None:
            return

        counts = self.pending_counts[channel_id]
        pending = counts.get(user_id, 0) - 1
        if pending > 0:
            counts[user_id] = pending
        else:
            counts.pop(user_id, None)

        user_signatures = self.pending_signatures[channel_id].get(user_id)
        if not user_signatures:
            return

        current_time = time.time()
        for signature, signature_timestamps in list(user_signatures.items()):
            self._prune_signature_timestamps(signature_timestamps, current_time)
            if not signature_timestamps:
                user_signatures.pop(signature, None)

        if not user_signatures:
            self.pending_signatures[channel_id].pop(user_id, None)
    
    def set_processor(self, callback: Callable):
        """Set the callback function to process requests."""
        self.process_callback = callback
    
    async def add_request(
        self,
        channel_id: LocalScopeId,
        message: discord.Message,
        content: str,
        guild: discord.Guild,
        attachments: List[discord.Attachment],
        user_name: str,
        is_dm: bool,
        user_id: int,
        sticker_info: str = None,
        from_interact_command: bool = False,
        split_reply_target: discord.Member = None,
        forced_target_user_id: int = None,
        forced_target_user_name: str = None,
        allow_auto_reminders: bool = False,
        pending_reminder_clarification: dict = None,
        is_autonomous: bool = False,
        dm_invite_requested: bool = False,
        route_req_id: str = None,
        scope_key: ScopeKey | None = None,
    ) -> bool:
        """Add a request to the queue. Returns True if added, False if spam."""

        async with self.locks[channel_id]:
            current_time = time.time()
            req_id = route_req_id or log.new_request_id()

            # Pre-compute stripped content once for comparisons
            content_stripped = content.strip()
            target_id = self._split_target_id(split_reply_target)
            request_signature = (content_stripped, target_id)
            channel_signatures = self.pending_signatures[channel_id][user_id]
            signature_timestamps = channel_signatures[request_signature]
            self._prune_signature_timestamps(signature_timestamps, current_time)

            # Check for duplicate requests from same user (spam prevention)
            if signature_timestamps:
                log.diagnostic(
                    "Request rejected as duplicate",
                    component="queue",
                    event="request_rejected",
                    req_id=req_id,
                    channel_id=channel_id,
                    user_id=user_id,
                    reason="duplicate_signature",
                    pending_count=self.pending_counts[channel_id][user_id],
                )
                return False

            # Limit pending requests per user
            if self.pending_counts[channel_id][user_id] >= 3:
                log.diagnostic(
                    "Request rejected by per-user pending limit",
                    component="queue",
                    event="request_rejected",
                    req_id=req_id,
                    channel_id=channel_id,
                    user_id=user_id,
                    reason="pending_limit",
                    pending_count=self.pending_counts[channel_id][user_id],
                )
                return False

            # Add request to queue
            self._next_request_id[channel_id] += 1
            envelope = RequestEnvelope(
                id=self._next_request_id[channel_id],
                correlation_id=req_id,
                timestamp=current_time,
                channel_id=channel_id,
                scope_key=scope_key,
                message=message,
                content=content,
                content_stripped=content_stripped,
                request_signature=request_signature,
                guild=guild,
                attachments=tuple(attachments or ()),
                user_name=user_name,
                is_dm=is_dm,
                user_id=user_id,
                sticker_info=sticker_info,
                from_interact_command=from_interact_command,
                direct_target=split_reply_target,
                forced_target_user_id=forced_target_user_id,
                forced_target_user_name=forced_target_user_name,
                allow_auto_reminders=allow_auto_reminders,
                pending_reminder_clarification=dict(pending_reminder_clarification) if pending_reminder_clarification else None,
                is_autonomous=is_autonomous,
                dm_invite_requested=dm_invite_requested,
            )
            request = envelope.to_legacy_dict()

            self.queues[channel_id].append(request)
            self.pending_counts[channel_id][user_id] += 1
            signature_timestamps.append(current_time)
            log.diagnostic(
                "Request queued",
                component="queue",
                event="request_queued",
                req_id=req_id,
                channel_id=channel_id,
                user_id=user_id,
                queue_depth=len(self.queues[channel_id]),
                pending_count=self.pending_counts[channel_id][user_id],
                is_dm=is_dm,
                is_autonomous=is_autonomous,
                from_interact_command=from_interact_command,
                split_target_id=target_id,
                scope_history_id=str(scope_key.history_id) if scope_key else None,
                content_len=len(content_stripped),
                attachments_count=len(attachments or []),
            )

            # Start processing if not already
            if not self.processing[channel_id]:
                asyncio.create_task(self._process_queue(channel_id))

            return True
    
    async def _process_queue(self, channel_id: LocalScopeId):
        """Process all requests in the queue for a channel."""
        
        async with self.locks[channel_id]:
            if self.processing[channel_id]:
                return
            self.processing[channel_id] = True
        
        try:
            while self.queues[channel_id]:
                # Get next request
                async with self.locks[channel_id]:
                    if not self.queues[channel_id]:
                        break
                    request = self.queues[channel_id].popleft()
                    log.diagnostic(
                        "Request dequeued",
                        component="queue",
                        event="request_dequeued",
                        req_id=request.get("req_id"),
                        channel_id=channel_id,
                        user_id=request.get("user_id"),
                        queue_depth=len(self.queues[channel_id]),
                        wait_ms=int((time.time() - request.get("timestamp", time.time())) * 1000),
                    )

                try:
                    # Process the request while its pending slot/signature remain active.
                    if self.process_callback:
                        await self.process_callback(request)
                finally:
                    async with self.locks[channel_id]:
                        self._release_request_tracking(channel_id, request)
                
                # Small delay between requests
                await asyncio.sleep(0.5)
                
        finally:
            async with self.locks[channel_id]:
                self.processing[channel_id] = False
                # Check if new requests arrived while we were finishing up
                # This fixes a race condition where requests could get stuck
                if self.queues[channel_id]:
                    asyncio.create_task(self._process_queue(channel_id))
