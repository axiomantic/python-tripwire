"""bigfoot: a pluggable interaction auditor for Python tests."""

from bigfoot._errors import (
    ConflictError,
    InteractionMismatchError,
    BigfootError,
    SandboxNotActiveError,
    UnassertedInteractionsError,
    UnmockedInteractionError,
    UnusedMocksError,
    VerificationError,
)
from bigfoot._mock_plugin import MockPlugin
from bigfoot._verifier import InAnyOrderContext, SandboxContext, StrictVerifier

try:
    from bigfoot.plugins.http import HttpPlugin  # noqa: F401
except ImportError:  # pragma: no cover
    pass  # http extra not installed

__all__ = [
    "StrictVerifier",
    "SandboxContext",
    "InAnyOrderContext",
    "MockPlugin",
    "BigfootError",
    "UnmockedInteractionError",
    "UnassertedInteractionsError",
    "UnusedMocksError",
    "VerificationError",
    "InteractionMismatchError",
    "SandboxNotActiveError",
    "ConflictError",
]
