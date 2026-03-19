"""Run a linter via subprocess.Popen."""

import subprocess


def run_linter(path: str) -> tuple[int, str]:
    """Run ruff on the given path, return (returncode, output)."""
    proc = subprocess.Popen(
        ["ruff", "check", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = proc.communicate()
    return proc.returncode, stdout.decode()
