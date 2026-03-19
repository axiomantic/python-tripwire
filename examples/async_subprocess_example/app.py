"""Run a linter via asyncio.create_subprocess_exec."""

import asyncio


async def run_linter(path: str) -> tuple[int, str]:
    """Run ruff on the given path asynchronously, return (returncode, output)."""
    proc = await asyncio.create_subprocess_exec(
        "ruff", "check", path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode()
