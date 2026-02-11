"""Async utility functions shared across modules."""

import asyncio
import contextlib
import logging
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)


def run_async_in_thread(coro: Coroutine[Any, Any, T]) -> T:
    """Run async code in a thread without any executor shutdown.

    Use this instead of asyncio.run() when running async code from
    within a thread spawned by asyncio.to_thread() or run_in_executor().
    """
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def run_async_with_timeout(coro: Coroutine[Any, Any, T], executor_timeout: float = 10.0) -> T:
    """Run async code like asyncio.run() but with timeout on executor shutdown."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        with contextlib.suppress(Exception):
            loop.run_until_complete(loop.shutdown_asyncgens())

        try:
            loop.run_until_complete(
                asyncio.wait_for(
                    loop.shutdown_default_executor(),
                    timeout=executor_timeout,
                )
            )
        except TimeoutError:
            logger.warning(
                "Executor shutdown timed out after %.1fs", executor_timeout
            )
        except Exception as e:
            logger.debug("Executor shutdown error (ignored): %s", e)

        asyncio.set_event_loop(None)
        loop.close()


async def delayed_invoke(delay: float, coro: Coroutine[Any, Any, Any]) -> Any:
    """Execute coroutine after a delay. Used for staggered parallel execution."""
    if delay > 0:
        await asyncio.sleep(delay)
    return await coro
