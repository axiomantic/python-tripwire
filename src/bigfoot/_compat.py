"""Python version compatibility shims."""

from __future__ import annotations

import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

if sys.version_info >= (3, 11):
    from builtins import BaseExceptionGroup
else:
    from exceptiongroup import BaseExceptionGroup

__all__ = ["tomllib", "BaseExceptionGroup"]
