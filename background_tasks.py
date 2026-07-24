"""
Named background task ownership for one bot runtime.

Discord fires ``on_ready`` again after any reconnect that cannot resume, and the
handler starts this runtime's loops. A second copy of a loop sends the same
bot-initiated message twice, because both copies read the send-once state before
either writes it. This registry keeps one live task per name so a reconnect
cannot duplicate a loop.
"""

from __future__ import annotations

import asyncio

import logger as log


class BackgroundTaskRegistry:
    """Track one running task per name, with cancel-all for shutdown."""

    def __init__(self, owner_label: str = ""):
        self._owner_label = owner_label
        self._tasks: dict[str, asyncio.Task] = {}

    def __len__(self) -> int:
        return len(self._tasks)

    def __iter__(self):
        return iter(list(self._tasks.values()))

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tasks)

    def is_running(self, name: str) -> bool:
        task = self._tasks.get(name)
        return task is not None and not task.done()

    def start_once(self, coro, *, name: str) -> asyncio.Task:
        """Schedule ``coro`` unless a task of the same name is still running.

        The already-running task wins and the surplus coroutine is closed, so a
        duplicate start neither schedules work nor leaks a never-awaited
        coroutine.
        """
        running = self._tasks.get(name)
        if running is not None and not running.done():
            coro.close()
            log.debug(
                f"Background task '{name}' already running, skipping duplicate start",
                self._owner_label or None,
                component="lifecycle",
                event="background_task_duplicate_skipped",
            )
            return running

        task = asyncio.create_task(coro, name=f"{self._owner_label}:{name}" if self._owner_label else name)
        self._tasks[name] = task

        def _forget(finished: asyncio.Task) -> None:
            if self._tasks.get(name) is finished:
                self._tasks.pop(name, None)

        task.add_done_callback(_forget)
        return task

    async def cancel_all(self) -> None:
        """Cancel every live task and forget all names."""
        tasks = [task for task in self._tasks.values() if not task.done()]
        self._tasks.clear()
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
