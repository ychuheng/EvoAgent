"""Finish sandbox cleanup before propagating a caller's cancellation."""

import asyncio
from collections.abc import Awaitable


async def finish_on_cancel[T](awaitable: Awaitable[T]) -> T:
    """Wait for a short cleanup even if the awaiting task is cancelled.

    ``shield`` alone lets the caller exit while the cleanup continues in the
    background. That can leave the execution row locked or dispose its database
    before the final status is committed.
    """

    operation = asyncio.ensure_future(awaitable)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(operation)
            break
        except asyncio.CancelledError:
            if operation.done():
                # Preserve an error from cleanup instead of silently abandoning it.
                result = operation.result()
                cancelled = True
                break
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result
