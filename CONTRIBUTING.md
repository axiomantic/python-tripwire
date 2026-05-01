# Contributing to tripwire

Thanks for your interest in contributing to tripwire! This guide will help you get started.

## Development Setup

```bash
# Clone the repo
git clone https://github.com/axiomantic/pytest-tripwire.git
cd tripwire

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# Install in development mode with all extras
pip install -e ".[dev]"
```

## Running Tests

```bash
# Run the full test suite
python -m pytest tests/

# Run a specific test file
python -m pytest tests/unit/test_http_plugin.py

# Run with coverage
python -m pytest tests/ --cov
```

## Linting and Type Checking

```bash
# Lint
ruff check src/ tests/

# Auto-fix lint issues
ruff check --fix src/ tests/

# Type check
mypy src/
```

## Making Changes

1. **Fork the repo** and create a branch from `main`.
2. **Write tests first.** tripwire uses test-driven development. Every new feature or bug fix needs tests.
3. **Run the full test suite** before submitting. All tests must pass.
4. **Run linting and type checking.** Zero warnings required.
5. **Keep commits focused.** One logical change per commit.

## Pull Requests

- Keep PR titles short and descriptive.
- Include a summary of what changed and why in the PR description.
- If your PR adds a new plugin, include:
  - The plugin implementation in `src/tripwire/plugins/`
  - Unit tests in `tests/unit/`
  - A README section documenting the plugin
  - A mkdocs guide in `docs/guides/`
  - An API reference page in `docs/reference/`

## Writing Plugins

See the [Writing Plugins](https://axiomantic.github.io/tripwire/guides/writing-plugins/) guide for the full protocol. Key points:

- Subclass `BasePlugin` and implement all abstract methods.
- Every field in `interaction.details` must be assertable. No silent fields.
- Never auto-assert interactions. The test author must call `assert_interaction()` explicitly.
- Provide typed assertion helper methods on your proxy for ergonomic usage.

## Code Style

- Python 3.11+ features are welcome.
- Type annotations required on all public APIs.
- No `Any` types in production code (tests are fine).
- Follow existing patterns in the codebase.

## Reporting Issues

- Use the [issue tracker](https://github.com/axiomantic/pytest-tripwire/issues).
- For bugs, include: Python version, tripwire version, minimal reproduction, and full traceback.
- For feature requests, describe the use case and why existing plugins don't cover it.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
