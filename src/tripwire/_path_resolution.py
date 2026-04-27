"""Path resolution for import-site mocking.

Resolves colon-separated paths like 'module.path:attr.path' to
(parent_object, attr_name) tuples for setattr-based patching.
"""

import importlib
from typing import Any


def resolve_target(path: str) -> tuple[Any, str]:
    """Resolve a colon-separated path to (parent_object, attr_name).

    Returns the object on which setattr will be called and the attribute name.
    Does NOT resolve at registration time -- called at activation time only.

    Args:
        path: A colon-separated path like 'module.path:attr.path'.

    Returns:
        A tuple of (parent_object, attr_name).

    Raises:
        ValueError: If path format is invalid (missing colon).
        ImportError: If module cannot be imported.
        AttributeError: If any intermediate attr does not exist.
    """
    if ":" not in path:
        raise ValueError(
            f"Mock path {path!r} must use colon-separated format: "
            f"'module.path:attr.path'. Example: 'myapp.services:cache'"
        )

    module_path, attr_path = path.split(":", 1)

    # Import the module
    module = importlib.import_module(module_path)

    # Walk the attr chain
    parts = attr_path.split(".")
    if len(parts) == 1:
        # Simple case: "mod:attr" -> parent=module, attr_name="attr"
        # Validate the attribute exists
        getattr(module, parts[0])
        return module, parts[0]

    # Dotted case: "mod:Cls.method" -> parent=mod.Cls, attr_name="method"
    parent: Any = module
    for part in parts[:-1]:
        parent = getattr(parent, part)

    # Validate the final attribute exists
    getattr(parent, parts[-1])
    return parent, parts[-1]
