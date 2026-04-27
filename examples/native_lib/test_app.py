"""Test native library calls using tripwire native_mock."""

import tripwire

from .app import compute_distance


def test_compute_distance():
    tripwire.native_mock.mock_call("libm", "sqrt", returns=5.0)

    with tripwire:
        result = compute_distance(0.0, 0.0, 3.0, 4.0)

    assert result == 5.0

    tripwire.native_mock.assert_call(
        library="libm", function="sqrt", args=(25.0,),
    )
