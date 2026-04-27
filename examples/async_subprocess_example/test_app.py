"""Test run_linter using tripwire async_subprocess_mock."""

import tripwire

from .app import run_linter


async def test_linter_clean():
    (tripwire.async_subprocess_mock
        .new_session()
        .expect("spawn",       returns=None)
        .expect("communicate", returns=(b"All checks passed.\n", b"", 0)))

    with tripwire:
        rc, output = await run_linter("src/")

    assert rc == 0
    assert output == "All checks passed.\n"

    tripwire.async_subprocess_mock.assert_spawn(
        command=["ruff", "check", "src/"], stdin=None
    )
    tripwire.async_subprocess_mock.assert_communicate(input=None)
