import asyncio


async def connect_when_ready(client, loop, interval=0.1):
    """Retry refused startup connections, never an established session."""
    while True:
        try:
            await client.connect(loop)
            return
        except ConnectionRefusedError:
            if client.connected:
                raise
            await asyncio.sleep(interval)


async def wait_for_startup(connect, process, timeout=60):
    """Bound startup and stop immediately when the inferior Lisp exits."""
    connection = asyncio.ensure_future(connect)
    exited = asyncio.ensure_future(process.wait())
    try:
        done, _ = await asyncio.wait(
            (connection, exited), timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED)
        if exited in done:
            raise RuntimeError(
                "Inferior Lisp exited during startup (code {}). "
                "See its output in the Sublime console.".format(exited.result()))
        if connection in done:
            return await connection
        raise TimeoutError(
            "Slynk startup timed out after {} seconds. "
            "See the Lisp output in the Sublime console.".format(timeout))
    finally:
        for task in (connection, exited):
            if not task.done():
                task.cancel()
        await asyncio.gather(connection, exited, return_exceptions=True)
