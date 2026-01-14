"""
Discord Pals - Request Queue
Manages queued requests to avoid spam.
"""

import asyncio
import time
from typing import Dict, List, Callable, Any
from collections import defaultdict
import discord


class RequestQueue:
    """Manages queued requests with safe-locking to prevent spam responses."""
    
    def __init__(self):
        self.queues: Dict[int, List[dict]] = defaultdict(list)
        self.processing: Dict[int, bool] = defaultdict(bool)
        self.locks: Dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.process_callback: Callable = None
    
    def set_processor(self, callback: Callable):
        """Set the callback function to process requests."""
        self.process_callback = callback
    
    async def add_request(
        self,
        channel_id: int,
        message: discord.Message,
        content: str,
        guild: discord.Guild,
        attachments: List[discord.Attachment],
        user_name: str,
        is_dm: bool,
        user_id: int,
        reply_to_name: tuple = None,
        sticker_info: str = None
    ) -> bool:
        """Add a request to the queue. Returns True if added, False if spam."""

        async with self.locks[channel_id]:
            current_time = time.time()

            # Pre-compute stripped content once for comparisons
            content_stripped = content.strip()

            # Check for duplicate requests from same user (spam prevention)
            for queued in self.queues[channel_id]:
                if (queued['user_id'] == user_id and
                    current_time - queued['timestamp'] < 3 and
                    queued['content_stripped'] == content_stripped):
                    return False

            # Limit pending requests per user
            user_pending = sum(1 for req in self.queues[channel_id] if req['user_id'] == user_id)
            if user_pending >= 2:
                return False

            # Add request to queue
            request = {
                'id': len(self.queues[channel_id]) + int(current_time),
                'timestamp': current_time,
                'message': message,
                'content': content,
                'content_stripped': content_stripped,  # Pre-computed for duplicate checks
                'guild': guild,
                'attachments': list(attachments) if attachments else [],
                'user_name': user_name,
                'is_dm': is_dm,
                'user_id': user_id,
                'reply_to_name': reply_to_name,
                'sticker_info': sticker_info
            }

            self.queues[channel_id].append(request)

            # Start processing if not already
            if not self.processing[channel_id]:
                asyncio.create_task(self._process_queue(channel_id))

            return True
    
    async def _process_queue(self, channel_id: int):
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
                    request = self.queues[channel_id].pop(0)
                
                # Process the request
                if self.process_callback:
                    await self.process_callback(request)
                
                # Small delay between requests
                await asyncio.sleep(0.5)
                
        finally:
            async with self.locks[channel_id]:
                self.processing[channel_id] = False
                # Check if new requests arrived while we were finishing up
                # This fixes a race condition where requests could get stuck
                if self.queues[channel_id]:
                    asyncio.create_task(self._process_queue(channel_id))
