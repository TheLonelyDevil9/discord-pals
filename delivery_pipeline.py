"""
Typed response delivery foundations.

This module deliberately stops before Discord integration. Callers provide the
actual send function, and this boundary only decides which parts are confirmed,
which text is safe to persist, and whether one safe retry is allowed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import Enum


class DeliveryState(str, Enum):
    """Final state for a multipart delivery attempt."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class DeliveryNotSentError(Exception):
    """Raised when the caller knows no message was created for this part."""


@dataclass(frozen=True)
class DeliveryPlan:
    """Trusted visible parts plus caller-owned correlation fields."""

    visible_parts: tuple[str, ...]
    correlation_id: str
    idempotency_key: str
    persist_separator: str = "\n\n"

    def __post_init__(self) -> None:
        parts = tuple(self.visible_parts)
        if any(not part for part in parts):
            raise ValueError("DeliveryPlan visible_parts cannot contain empty text")
        if not self.correlation_id:
            raise ValueError("DeliveryPlan correlation_id is required")
        if not self.idempotency_key:
            raise ValueError("DeliveryPlan idempotency_key is required")

        object.__setattr__(self, "visible_parts", parts)

    @classmethod
    def from_parts(
        cls,
        visible_parts: Sequence[str],
        *,
        correlation_id: str,
        idempotency_key: str,
        persist_separator: str = "\n\n",
    ) -> "DeliveryPlan":
        return cls(
            visible_parts=tuple(visible_parts),
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            persist_separator=persist_separator,
        )


@dataclass(frozen=True)
class DeliverySendRequest:
    """One visible part sent by an injected transport."""

    visible_text: str
    part_index: int
    attempt: int
    correlation_id: str
    idempotency_key: str
    part_idempotency_key: str


@dataclass(frozen=True)
class ConfirmedDeliveryPart:
    """A part is confirmed only when the transport returns a message id."""

    part_index: int
    visible_text: str
    message_id: str


@dataclass(frozen=True)
class DeliveryOutcome:
    """Persistable delivery result with only confirmed visible content."""

    state: DeliveryState
    correlation_id: str
    idempotency_key: str
    confirmed_parts: tuple[ConfirmedDeliveryPart, ...]
    persistable_visible_text: str
    retry_count: int = 0
    failed_part_index: int | None = None
    ambiguous_part_index: int | None = None
    error: str | None = None

    @property
    def confirmed_message_ids(self) -> tuple[str, ...]:
        return tuple(part.message_id for part in self.confirmed_parts)


DeliverySendCallable = Callable[[DeliverySendRequest], Awaitable[str]]


async def deliver_multipart_response(
    plan: DeliveryPlan,
    send_part: DeliverySendCallable,
    *,
    max_retries: int = 1,
) -> DeliveryOutcome:
    """
    Send visible parts with the locked retry policy.

    A returned message id confirms a part. A clear DeliveryNotSentError can be
    retried once from the first unconfirmed part. Any other exception or a
    missing message id is ambiguous, so that same part is not retried.
    """
    if max_retries < 0:
        raise ValueError("max_retries cannot be negative")

    if not plan.visible_parts:
        return _build_outcome(plan, (), DeliveryState.SUCCESS, retry_count=0)

    confirmed_parts: list[ConfirmedDeliveryPart] = []
    next_part_index = 0
    retry_count = 0

    while next_part_index < len(plan.visible_parts):
        try:
            next_part_index = await _send_remainder_once(
                plan,
                send_part,
                start_index=next_part_index,
                attempt=retry_count,
                confirmed_parts=confirmed_parts,
            )
        except DeliveryNotSentError as exc:
            failed_index = len(confirmed_parts)
            if retry_count < max_retries:
                retry_count += 1
                next_part_index = failed_index
                continue

            return _build_outcome(
                plan,
                confirmed_parts,
                _state_for_confirmed_parts(plan, confirmed_parts),
                retry_count=retry_count,
                failed_part_index=failed_index,
                error=str(exc) or exc.__class__.__name__,
            )
        except asyncio.CancelledError:
            if not confirmed_parts:
                raise

            ambiguous_index = len(confirmed_parts)
            return _build_outcome(
                plan,
                confirmed_parts,
                _state_for_confirmed_parts(plan, confirmed_parts),
                retry_count=retry_count,
                ambiguous_part_index=ambiguous_index,
                error="CancelledError",
            )
        except Exception as exc:
            ambiguous_index = len(confirmed_parts)
            return _build_outcome(
                plan,
                confirmed_parts,
                _state_for_confirmed_parts(plan, confirmed_parts),
                retry_count=retry_count,
                ambiguous_part_index=ambiguous_index,
                error=str(exc) or exc.__class__.__name__,
            )

    return _build_outcome(plan, confirmed_parts, DeliveryState.SUCCESS, retry_count=retry_count)


async def _send_remainder_once(
    plan: DeliveryPlan,
    send_part: DeliverySendCallable,
    *,
    start_index: int,
    attempt: int,
    confirmed_parts: list[ConfirmedDeliveryPart],
) -> int:
    for part_index in range(start_index, len(plan.visible_parts)):
        visible_text = plan.visible_parts[part_index]
        message_id = await send_part(
            DeliverySendRequest(
                visible_text=visible_text,
                part_index=part_index,
                attempt=attempt,
                correlation_id=plan.correlation_id,
                idempotency_key=plan.idempotency_key,
                part_idempotency_key=f"{plan.idempotency_key}:{part_index}",
            )
        )
        if not message_id:
            raise RuntimeError("delivery send returned no message id")

        confirmed_parts.append(
            ConfirmedDeliveryPart(
                part_index=part_index,
                visible_text=visible_text,
                message_id=str(message_id),
            )
        )

    return len(plan.visible_parts)


def _state_for_confirmed_parts(
    plan: DeliveryPlan,
    confirmed_parts: Sequence[ConfirmedDeliveryPart],
) -> DeliveryState:
    if not confirmed_parts:
        return DeliveryState.FAILED
    if len(confirmed_parts) == len(plan.visible_parts):
        return DeliveryState.SUCCESS
    return DeliveryState.PARTIAL


def _build_outcome(
    plan: DeliveryPlan,
    confirmed_parts: Sequence[ConfirmedDeliveryPart],
    state: DeliveryState,
    *,
    retry_count: int,
    failed_part_index: int | None = None,
    ambiguous_part_index: int | None = None,
    error: str | None = None,
) -> DeliveryOutcome:
    confirmed_tuple = tuple(confirmed_parts)
    return DeliveryOutcome(
        state=state,
        correlation_id=plan.correlation_id,
        idempotency_key=plan.idempotency_key,
        confirmed_parts=confirmed_tuple,
        persistable_visible_text=plan.persist_separator.join(
            part.visible_text for part in confirmed_tuple
        ),
        retry_count=retry_count,
        failed_part_index=failed_part_index,
        ambiguous_part_index=ambiguous_part_index,
        error=error,
    )
