import asyncio

import pytest

from evoagent.sandbox.cancellation import finish_on_cancel


async def test_cancelled_caller_waits_for_cleanup_to_finish():
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def cleanup():
        started.set()
        await release.wait()
        finished.set()

    caller = asyncio.create_task(finish_on_cancel(cleanup()))
    await started.wait()
    caller.cancel()
    await asyncio.sleep(0)
    assert not caller.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert finished.is_set()


async def test_cleanup_error_is_reported_after_cancellation():
    started = asyncio.Event()
    release = asyncio.Event()

    async def cleanup():
        started.set()
        await release.wait()
        raise ValueError("cleanup failed")

    caller = asyncio.create_task(finish_on_cancel(cleanup()))
    await started.wait()
    caller.cancel()
    release.set()
    with pytest.raises(ValueError, match="cleanup failed"):
        await caller
