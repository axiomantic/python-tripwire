# CLI Tool Example

Demonstrates bigfoot's subprocess plugin for mocking `subprocess.run`
and `shutil.which`.

The application module (`app.py`) locates `gcc`, compiles a source file,
and runs the resulting binary. The test (`test_app.py`) uses
`bigfoot.subprocess_mock` to intercept both `which` and `run` calls,
verifying the exact commands and their ordering.

Run: `python -m pytest examples/cli_tool/ -v`
