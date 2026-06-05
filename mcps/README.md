# mcps/ — builtin MCP servers

This directory holds **MCP servers that ship with the repo**. They are plain
stdio MCP servers (their own subprocess), separate from the in-process `native`
tools and `skills/`.

## How discovery works

On startup `load_builtin_mcp_capabilities` (`app/capabilities/mcp/builtin.py`)
scans this directory. Every immediate subdirectory that contains a `server.py`
becomes one capability:

```
mcps/<name>/server.py   ->   capability  mcp:<name>   (tools prefixed with <name>_)
```

The server is launched lazily over stdio with the current Python interpreter
(`python mcps/<name>/server.py`); no subprocess is spawned until an agent that
enabled the capability actually runs.

This is complementary to `config/mcp.json`: use `mcps/` for servers whose code
lives in this repo, and `config/mcp.json` for external/remote servers.

## Add a builtin server

1. Create `mcps/<name>/server.py`.
2. Build a `FastMCP` instance and register tools.
3. Run it over stdio in `__main__`.

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-server")

@mcp.tool()
def greet(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}!"

if __name__ == "__main__":
    mcp.run()  # defaults to stdio transport
```

Keep each server **self-contained** — do not import from the `app` package, so
it can run as an independent subprocess.

## Example

`text_tools/` is a working example with `word_count`, `char_count`, `slugify`
and `reverse`. Run it directly to sanity-check:

```bash
python mcps/text_tools/server.py
```

It will wait for MCP stdio traffic (Ctrl+C to quit). Once the app is running it
shows up as the `mcp:text_tools` capability, e.g. via `GET /api/v1/tools`.
