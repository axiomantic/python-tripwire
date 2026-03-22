# Configuration Guide

bigfoot reads project-level configuration from `pyproject.toml` under the `[tool.bigfoot]` table. Configuration is optional; bigfoot works with sensible defaults when no configuration is present.

## Config file discovery

bigfoot walks up from the current working directory to find the nearest `pyproject.toml`. It checks each directory from `Path.cwd()` through all parent directories, stopping at the first `pyproject.toml` it finds. If no file is found, all configuration values use their defaults.

```
my-project/
    pyproject.toml       <-- bigfoot finds this
    src/
        myapp/
            client.py
    tests/
        test_client.py   <-- tests run from here
```

## Configuration format

Plugin configuration lives under `[tool.bigfoot.<plugin_key>]`. Each plugin declares its own `config_key()` class method that determines which sub-table it reads from.

```toml
[tool.bigfoot.http]
require_response = true
```

The top-level `[tool.bigfoot]` table can contain plugin sub-tables. Unknown keys at any level are silently ignored for forward-compatibility.

## HTTP plugin configuration

The `HttpPlugin` is currently the only plugin that reads configuration. It maps to the `[tool.bigfoot.http]` section.

### `require_response`

When `true`, `assert_request()` returns an `HttpAssertionBuilder` that requires a chained `.assert_response()` call to complete the assertion. When `false` (the default), `assert_request()` is terminal and asserts only the request fields.

```toml
[tool.bigfoot.http]
require_response = true
```

**Type:** `bool`
**Default:** `false`

This setting can be overridden on a per-call basis:

```python
# Project config sets require_response = true, but override for this one call:
bigfoot.http.assert_request("GET", "https://api.example.com/health", require_response=False)
```

See the [HttpPlugin Guide](http-plugin.md) for details on the `require_response` feature.

## Per-call override vs project-level config

Project-level configuration sets the default behavior for all tests. Individual test assertions can override the project default by passing explicit keyword arguments.

For example, with `require_response = true` in `pyproject.toml`:

```python
# Uses the project default (require_response=True) -- must chain assert_response()
bigfoot.http.assert_request("GET", "https://api.example.com/users") \
    .assert_response(200, {}, "[]")

# Overrides the project default for this call only
bigfoot.http.assert_request("GET", "https://api.example.com/health", require_response=False)
```

## Error handling

If `pyproject.toml` exists but contains invalid TOML syntax, `tomllib.TOMLDecodeError` is raised. This is intentional: a malformed `pyproject.toml` is a user error that must not silently produce empty config.

```python
# This will raise tomllib.TOMLDecodeError, not return {}
# pyproject.toml with syntax errors
```

If `pyproject.toml` is valid but has no `[tool.bigfoot]` section, an empty dict is returned and all plugins use their defaults.

## How config loading works internally

Configuration loading follows this flow:

1. `StrictVerifier.__init__()` calls `load_bigfoot_config()` to find and parse `pyproject.toml`
2. The result is stored as `verifier._bigfoot_config`
3. Each plugin's `__init__()` checks its `config_key()` and calls `self.load_config(config_dict)` with the matching sub-table
4. `load_config()` validates and applies the configuration values

## Writing plugin configuration

If you are writing a custom plugin that needs configuration, implement two methods:

### `config_key()` class method

Return a string that maps to `[tool.bigfoot.<key>]`, or `None` to opt out of configuration:

```python
class MyPlugin(BasePlugin):
    @classmethod
    def config_key(cls) -> str | None:
        return "my_plugin"  # reads from [tool.bigfoot.my_plugin]
```

### `load_config()` method

Override `load_config()` to validate and apply your configuration:

```python
class MyPlugin(BasePlugin):
    def load_config(self, config: dict[str, Any]) -> None:
        if "timeout" in config:
            val = config["timeout"]
            if not isinstance(val, (int, float)):
                raise TypeError(
                    f"[tool.bigfoot.my_plugin] timeout must be a number, "
                    f"got {type(val).__name__}"
                )
            self._timeout = val
```

The `load_config()` method is called as the last step of the plugin's `__init__()`, after all instance attributes have been set. The default implementation in `BasePlugin` is a no-op.

## Disabling Built-in Plugins

If built-in plugins interfere with your custom plugin's tests, disable them:

```toml
[tool.bigfoot]
disabled_plugins = ["socket", "subprocess"]
```

See [Writing Plugins](writing-plugins.md) for the plugin authoring guide.

## Example pyproject.toml

```toml
[project]
name = "my-app"
version = "1.0.0"

[tool.bigfoot.http]
require_response = true
```
