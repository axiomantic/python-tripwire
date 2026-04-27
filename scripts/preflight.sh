#!/usr/bin/env bash
# scripts/preflight.sh - Pre-C1 rename preflight checks.
# Run from the repo root. Exits non-zero on any failure so CI catches drift.
set -euo pipefail

echo "=== 1. Sentinel collision audit ==="
# Confirm no existing source ID already uses the new colon-namespace shape
# in a way that would clash with the post-rename sentinels.
grep -rn '"\(subprocess\|httpx\|socket\|asyncio\|http\|dns\|file_io\):' src/ tests/ \
    --include="*.py" || true   # informational; collisions logged for review

echo "=== 2. Version-resolution check ==="
# M-5: identify any dynamic version-resolution call that the rename pass must update.
# As of design time this returns nothing (no __version__ exposed, no
# importlib.metadata.version("bigfoot") in source). If this returns hits, the
# rename pass must include them.
grep -rn "__version__\|importlib\.metadata\.version" src/ pyproject.toml || true

echo "=== 3. Readthedocs URL audit ==="
# Find every readthedocs / docs URL that needs updating from bigfoot to tripwire.
grep -rn "bigfoot\.readthedocs\.io\|bigfoot.*docs" \
    src/ tests/ docs/ README.md mkdocs.yml pyproject.toml || true

echo "=== 4. Plugin enumeration via BasePlugin.__subclasses__() ==="
# I-1: confirm the live plugin enumeration matches the §4 migration table BEFORE
# the rename pass. If the live set diverges from the table, the table needs an
# update before C2 can land.
uv run python -c "
import bigfoot  # autodiscovery loads plugins
from bigfoot._base_plugin import BasePlugin
def _walk(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from _walk(sub)
names = sorted({c.__module__ + '.' + c.__qualname__ for c in _walk(BasePlugin)})
for n in names:
    print(n)
"

echo "=== 5. Worktree state ==="
git status --porcelain | (! grep . > /dev/null) || \
    { echo "FAIL: worktree not clean"; exit 1; }

echo "=== 6. Branch base ==="
git rev-parse --abbrev-ref HEAD | grep -q '^rename/tripwire-and-proposals$' || \
    { echo "FAIL: not on rename/tripwire-and-proposals branch"; exit 1; }

echo "=== Preflight OK ==="
