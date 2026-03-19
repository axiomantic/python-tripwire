"""Test run_linter using bigfoot popen_mock."""

import bigfoot

from .app import run_linter


def test_linter_clean():
    (bigfoot.popen_mock
        .new_session()
        .expect("spawn",       returns=None)
        .expect("communicate", returns=(b"All checks passed.\n", b"", 0)))

    with bigfoot:
        rc, output = run_linter("src/")

    assert rc == 0
    assert output == "All checks passed.\n"

    bigfoot.popen_mock.assert_spawn(command=["ruff", "check", "src/"], stdin=None)
    bigfoot.popen_mock.assert_communicate(input=None)
