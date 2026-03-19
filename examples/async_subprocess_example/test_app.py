"""Test run_linter using bigfoot async_subprocess_mock."""

import bigfoot

from .app import run_linter


async def test_linter_clean():
    (bigfoot.async_subprocess_mock
        .new_session()
        .expect("spawn",       returns=None)
        .expect("communicate", returns=(b"All checks passed.\n", b"", 0)))

    with bigfoot:
        rc, output = await run_linter("src/")

    assert rc == 0
    assert output == "All checks passed.\n"

    bigfoot.async_subprocess_mock.assert_spawn(
        command=["ruff", "check", "src/"], stdin=None
    )
    bigfoot.async_subprocess_mock.assert_communicate(input=None)
