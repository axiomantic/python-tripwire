"""Test native library calls using bigfoot native_mock."""

import bigfoot

from .app import compute_distance


def test_compute_distance():
    bigfoot.native_mock.mock_call("libm", "sqrt", returns=5.0)

    with bigfoot:
        result = compute_distance(0.0, 0.0, 3.0, 4.0)

    assert result == 5.0

    bigfoot.native_mock.assert_call(
        library="libm", function="sqrt", args=(25.0,),
    )
