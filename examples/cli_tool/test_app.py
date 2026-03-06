"""Test build_and_run using bigfoot subprocess mocking."""

import bigfoot

from .app import build_and_run


def test_build_and_run_compiles_and_executes():
    bigfoot.subprocess_mock.mock_which("gcc", returns="/usr/bin/gcc")
    bigfoot.subprocess_mock.mock_run(
        ["/usr/bin/gcc", "-o", "/tmp/out", "hello.c"], returncode=0
    )
    bigfoot.subprocess_mock.mock_run(
        ["/tmp/out"], returncode=0, stdout="Hello, world!\n"
    )

    with bigfoot:
        output = build_and_run("hello.c")

    assert output == "Hello, world!\n"

    bigfoot.assert_interaction(
        bigfoot.subprocess_mock.which, name="gcc", returns="/usr/bin/gcc"
    )
    bigfoot.assert_interaction(
        bigfoot.subprocess_mock.run,
        command=["/usr/bin/gcc", "-o", "/tmp/out", "hello.c"],
        returncode=0,
        stdout="",
        stderr="",
    )
    bigfoot.assert_interaction(
        bigfoot.subprocess_mock.run,
        command=["/tmp/out"],
        returncode=0,
        stdout="Hello, world!\n",
        stderr="",
    )


def test_build_and_run_raises_when_gcc_missing():
    bigfoot.subprocess_mock.mock_which("gcc", returns=None)

    with bigfoot:
        try:
            build_and_run("hello.c")
        except RuntimeError as exc:
            assert str(exc) == "gcc not found"
        else:
            raise AssertionError("Expected RuntimeError")

    bigfoot.assert_interaction(
        bigfoot.subprocess_mock.which, name="gcc", returns=None
    )
