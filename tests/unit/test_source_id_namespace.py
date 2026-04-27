"""C1-T6: every plugin's source_id constants match the colon-namespaced shape.

Per the locked C-4 decision the convention is `<library>:<method>` or
`<library>:<sub>:<method>` (e.g. `subprocess:run`, `subprocess:popen:spawn`,
`asyncio:subprocess:spawn`). The `tripwire:` prefix is intentionally absent
because the namespace is implicit inside the tripwire package.
"""

from __future__ import annotations

import importlib
import re

from tripwire._registry import PLUGIN_REGISTRY, _is_available

# The design doc cites `^[a-z_]+:[a-z_]+(:[a-z_]+)?$` for the colon-namespace
# convention. Library names containing digits (e.g., `psycopg2`, `boto3`) are
# part of the existing surface area, so the segment character class accepts
# `[a-z0-9_]` rather than `[a-z_]`. The structural shape (two or three
# colon-separated lowercase segments, no leading `tripwire:` prefix) is what
# the C-4 decision actually constrains.
_SOURCE_ID_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*:[a-z_][a-z0-9_]*(:[a-z_][a-z0-9_]*)?$")


def test_source_ids_use_colon_namespace() -> None:
    """Each `_SOURCE_*` constant defined in a registered plugin module matches
    the `<library>:<method>` (or three-part) regex.

    Failure modes guarded:
    - Sentinel restructure missed a plugin.
    - The deprecated `tripwire:` prefix was reintroduced.
    """
    offenders: list[str] = []
    checked: list[str] = []

    for entry in PLUGIN_REGISTRY:
        if not _is_available(entry):
            continue
        module = importlib.import_module(entry.import_path)
        for attr_name in dir(module):
            if not attr_name.startswith("_SOURCE_"):
                continue
            value = getattr(module, attr_name)
            if not isinstance(value, str):
                continue
            checked.append(f"{entry.import_path}.{attr_name}={value!r}")
            if not _SOURCE_ID_PATTERN.match(value):
                offenders.append(
                    f"{entry.import_path}.{attr_name} = {value!r} "
                    f"(does not match {_SOURCE_ID_PATTERN.pattern})"
                )

    assert checked, "No _SOURCE_* constants were inspected; registry import failed?"
    assert offenders == [], (
        "Source IDs violate the colon-namespace convention:\n"
        + "\n".join(offenders)
    )
