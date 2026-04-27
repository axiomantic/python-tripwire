"""Test build_and_run using tripwire subprocess mocking."""

import tripwire

from .app import build_and_run


def test_build_and_run_compiles_and_executes():
    tripwire.subprocess_mock.mock_which("gcc", returns="/usr/bin/gcc")
    tripwire.subprocess_mock.mock_run(
        ["/usr/bin/gcc", "-o", "/tmp/out", "hello.c"], returncode=0
    )
    tripwire.subprocess_mock.mock_run(
        ["/tmp/out"], returncode=0, stdout="Hello, world!\n"
    )

    with tripwire:
        output = build_and_run("hello.c")

    assert output == "Hello, world!\n"

    tripwire.assert_interaction(
        tripwire.subprocess_mock.which, name="gcc", returns="/usr/bin/gcc"
    )
    tripwire.assert_interaction(
        tripwire.subprocess_mock.run,
        command=["/usr/bin/gcc", "-o", "/tmp/out", "hello.c"],
        returncode=0,
        stdout="",
        stderr="",
    )
    tripwire.assert_interaction(
        tripwire.subprocess_mock.run,
        command=["/tmp/out"],
        returncode=0,
        stdout="Hello, world!\n",
        stderr="",
    )


def test_build_and_run_raises_when_gcc_missing():
    tripwire.subprocess_mock.mock_which("gcc", returns=None)

    with tripwire:
        try:
            build_and_run("hello.c")
        except RuntimeError as exc:
            assert str(exc) == "gcc not found"
        else:
            raise AssertionError("Expected RuntimeError")

    tripwire.assert_interaction(
        tripwire.subprocess_mock.which, name="gcc", returns=None
    )
