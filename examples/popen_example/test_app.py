"""Test run_linter using tripwire popen_mock."""

import tripwire

from .app import run_linter


def test_linter_clean():
    (tripwire.popen_mock
        .new_session()
        .expect("spawn",       returns=None)
        .expect("communicate", returns=(b"All checks passed.\n", b"", 0)))

    with tripwire:
        rc, output = run_linter("src/")

    assert rc == 0
    assert output == "All checks passed.\n"

    tripwire.popen_mock.assert_spawn(command=["ruff", "check", "src/"], stdin=None)
    tripwire.popen_mock.assert_communicate(input=None)
