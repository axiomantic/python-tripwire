"""CLI tool that compiles and runs a program."""

import shutil
import subprocess


def build_and_run(source: str) -> str:
    """Find gcc, compile source, run the binary."""
    gcc = shutil.which("gcc")
    if gcc is None:
        raise RuntimeError("gcc not found")
    subprocess.run([gcc, "-o", "/tmp/out", source], check=True)
    result = subprocess.run(["/tmp/out"], capture_output=True, text=True, check=True)
    return result.stdout
