"""
Silent retry policy for failed generations.

The provider manager already walks every configured tier before giving up. This
boundary adds whole-generation retries on top of that so a transient outage is
retried quietly instead of surfacing a public failure notice on the first miss.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import logger as log


@dataclass(frozen=True)
class ProviderRetryPolicy:
    """Attempt budget and pacing for silent whole-generation retries."""

    attempts: int = 4
    window_seconds: float = 30.0
    total_deadline_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("ProviderRetryPolicy attempts must be at least 1")
        if self.window_seconds < 0:
            raise ValueError("ProviderRetryPolicy window_seconds cannot be negative")
        if self.total_deadline_seconds < 0:
            raise ValueError("ProviderRetryPolicy total_deadline_seconds cannot be negative")

    @property
    def retry_delay_seconds(self) -> float:
        """Even spacing that fits every retry inside the configured window."""
        if self.attempts < 2 or self.window_seconds <= 0:
            return 0.0
        return self.window_seconds / (self.attempts - 1)

    @classmethod
    def from_runtime_config(cls, config) -> "ProviderRetryPolicy":
        return cls(
            attempts=int(config.get("provider_retry_attempts", 4)),
            window_seconds=float(config.get("provider_retry_window_seconds", 30)),
            total_deadline_seconds=float(config.get("provider_total_deadline_seconds", 0) or 0),
        )


async def generate_with_silent_retry(
    generate: Callable[[], Awaitable[str | None]],
    *,
    policy: ProviderRetryPolicy,
    bot_name: str = None,
    req_id: str = None,
    should_retry: Callable[[], bool] = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str | None:
    """
    Run ``generate`` until it returns text or the attempt budget is spent.

    Nothing is announced between attempts. Callers own the public failure
    message and only see ``None`` once every attempt has been used, or once
    ``should_retry`` reports the failure is not a provider outage.
    """
    delay = policy.retry_delay_seconds
    deadline = (
        time.monotonic() + policy.total_deadline_seconds
        if policy.total_deadline_seconds > 0
        else None
    )

    for attempt in range(1, policy.attempts + 1):
        response = await generate()
        if response is not None:
            return response
        if should_retry is not None and not should_retry():
            return None
        if attempt >= policy.attempts:
            break
        if deadline is not None and time.monotonic() >= deadline:
            log.warn(
                f"Provider deadline reached after {attempt} attempt(s); not retrying",
                bot_name,
                component="provider",
                event="provider_retry_deadline_exceeded",
                req_id=req_id,
                attempt=attempt,
                max_attempts=policy.attempts,
            )
            return None

        log.warn(
            f"All providers failed (attempt {attempt}/{policy.attempts}), retrying in {delay:.1f}s",
            bot_name,
            component="provider",
            event="provider_retry_scheduled",
            req_id=req_id,
            attempt=attempt,
            max_attempts=policy.attempts,
            retry_delay_s=round(delay, 2),
        )
        if delay > 0:
            await sleep(delay)

    log.error(
        f"All providers failed after {policy.attempts} attempts",
        bot_name,
        component="provider",
        event="provider_retry_exhausted",
        req_id=req_id,
        max_attempts=policy.attempts,
    )
    return None
