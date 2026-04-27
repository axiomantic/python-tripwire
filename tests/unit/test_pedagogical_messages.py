"""C5-T1, C5-T2, C5-T3: Pedagogical GuardedCallError message framing + call site.

These tests are unit-level: they construct a GuardedCallError directly and
assert message content. The integration-level tests in
``tests/integration/test_pedagogical_messages.py`` exercise the live frame
walk through the dispatch path.
"""

from __future__ import annotations

from tripwire._errors import GuardedCallError
from tripwire._firewall_request import SubprocessFirewallRequest


def _build_err_with_frame(
    user_frame: tuple[str, int, str] | None,
    plugin: str = "subprocess",
    method: str = "run",
) -> GuardedCallError:
    return GuardedCallError(
        source_id=f"{plugin}:{method}",
        plugin_name=plugin,
        firewall_request=SubprocessFirewallRequest(
            command="/bin/true",
            binary="/bin/true",
        ),
        user_frame=user_frame,
    )


def test_message_contains_outside_framing() -> None:
    """C5-T1: framing line names 'OUTSIDE any "with tripwire:" block' literally."""
    err = _build_err_with_frame(("/path/to/test.py", 42, "test_x"))
    assert 'OUTSIDE any "with tripwire:" block' in str(err)


def test_message_names_plugin_and_method() -> None:
    """C5-T2: message contains plugin name and method-being-called.

    Uses sentinel values that would not naturally appear elsewhere in the
    rendered message (the literal ``source_id`` echo, the ``Attempted:``
    block, etc.), so the assertion catches a regression that drops the
    human-prose ``<plugin>.<method>`` rendering.
    """
    err = _build_err_with_frame(
        ("/path/to/test.py", 42, "test_x"),
        plugin="ZPLUG_SENTINEL",
        method="ZMETH_SENTINEL",
    )
    msg = str(err)
    assert "ZPLUG_SENTINEL.ZMETH_SENTINEL" in msg, (
        "expected plugin.method joined in human prose; got:\n" + msg
    )


def test_message_includes_user_call_site() -> None:
    """C5-T3: message renders ``at <file>:<lineno>`` for the user frame."""
    err = _build_err_with_frame(("/abs/path/test_caller.py", 137, "test_caller"))
    assert "at /abs/path/test_caller.py:137" in str(err)


def test_message_renders_unknown_call_site_when_frame_is_none() -> None:
    """When user_frame is None, message renders ``at <unknown call site>``."""
    err = _build_err_with_frame(None)
    assert "at <unknown call site>" in str(err)
