"""Shared fixtures for bigfoot examples."""

import logging

import pytest


@pytest.fixture(autouse=True)
def _enable_all_log_levels():
    """Set root logger to DEBUG so bigfoot's LoggingPlugin can intercept all levels.

    Python's logging module checks the effective level before calling Logger._log().
    bigfoot intercepts at the _log() level, so the logger must be configured to
    pass messages through to that point.
    """
    root = logging.getLogger()
    original_level = root.level
    root.setLevel(logging.DEBUG)
    yield
    root.setLevel(original_level)
