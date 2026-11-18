"""
Discord Pals - Failure Notices
Limits the public failure notices one bot posts in a channel.

During a provider outage every queued request in a channel fails in turn; one
notice per channel per interval tells the channel without one message per request.
"""

import time
from typing import Callable, Hashable

FAILURE_NOTICE_INTERVAL_SECONDS = 60.0


class FailureNoticeLimit:
    """Allow one failure notice per channel per interval."""

    def __init__(self, interval_seconds: float = FAILURE_NOTICE_INTERVAL_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self._interval_seconds = interval_seconds
        self._clock = clock
        self._last_notice: dict[Hashable, float] = {}

    def allow(self, channel_key: Hashable) -> bool:
        """Return True and record the notice when channel_key had none within the interval."""
        now = self._clock()
        for key, sent_at in list(self._last_notice.items()):
            if now - sent_at >= self._interval_seconds:
                del self._last_notice[key]
        if channel_key in self._last_notice:
            return False
        self._last_notice[channel_key] = now
        return True
