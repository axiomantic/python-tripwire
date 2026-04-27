"""C1-T4: with no config, unmocked I/O outside a sandbox raises GuardedCallError.

This is the integration check for the Proposal 1 default flip: previously
the same scenario only emitted a `GuardedCallWarning`. After C1 the default
is `error`, so the call must raise.
"""

from __future__ import annotations

import textwrap

import pytest

pytest_plugins = ["pytester"]

pytestmark = pytest.mark.integration


@pytest.mark.allow("subprocess")
def test_unmocked_call_raises_by_default(pytester: pytest.Pytester) -> None:
    """A project with no `[tool.tripwire]` config + an unmocked subprocess.run
    call outside `with tripwire:` raises GuardedCallError (NOT a warning).

    This must run pytester in a SUBPROCESS rather than in-process, because
    tripwire's `pytest_unconfigure` hook unconditionally uninstalls global
    context propagation; running an inner pytest in-process would tear down
    the parent session's shared state and corrupt later tests.
    """
    # Empty pyproject: no [tool.tripwire] section at all -> default applies.
    pytester.makepyprojecttoml("[project]\nname = \"client\"\nversion = \"0.0.0\"\n")
    pytester.makepyfile(
        test_unmocked=textwrap.dedent(
            """
            import subprocess

            import pytest

            from tripwire import GuardedCallError


            def test_unmocked_subprocess_raises():
                with pytest.raises(GuardedCallError):
                    subprocess.run(["true"])
            """
        )
    )
    # tripwire.pytest_plugin auto-loads via the pytest11 entry point in the
    # subprocess. Run subprocess to isolate global state lifecycle.
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(passed=1)
