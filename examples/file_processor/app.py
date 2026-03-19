"""Archive a directory and clean up the source."""

import os
import shutil
from pathlib import Path


def archive_and_clean(source_dir, archive_dir, manifest_path):
    """Copy source to archive, write a manifest, then remove the source."""
    os.makedirs(archive_dir, exist_ok=True)
    shutil.copytree(source_dir, os.path.join(archive_dir, "latest"))
    Path(manifest_path).write_text(f"archived: {source_dir}")
    shutil.rmtree(source_dir)
