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

## Builtin lifecycle

Builtin MCP records use `transport=builtin` and are managed with the program
that ships their implementation. The Console does not show a delete action for
them, and `DELETE /api/v1/mcp/{name}` returns `403`. To remove a builtin MCP,
remove its repository implementation and program configuration together in a
normal release. A database row using another transport remains an ordinary
external MCP even when its name matches a builtin directory.

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
cd deep_agent
python mcps/text_tools/server.py
```

It will wait for MCP stdio traffic (Ctrl+C to quit). Once the app is running it
shows up as the `mcp:text_tools` capability, e.g. via `GET /api/v1/tools`.

## DeepCube (`deepcube/`)

Thin stdio launcher for the installed `deepcube-mcp` package. Capability name:
`mcp:deepcube` (tools prefixed with `deepcube_`).

Startup defaults (override via `mcp_servers.config.env`):

| Variable | Default                                                         |
|----------|-----------------------------------------------------------------|
| `DEEPCUBE_MCP_TRANSPORT` | `stdio`                                                         |
| `DEEPCUBE_MCP_ENV_URL` | `https://test.deepfos.com/deepfos-server/`                      |
| `DEEPCUBE_MCP_HEADERS` | JSON object, e.g. `{"app":"demo","space":"demo","token":"..."}` |
| `DEEPCUBE_MCP_DIM_INIT_MAX_CONCURRENCY` | `4` (Cube dimension init concurrency)                           |

Example DB `config.env` for live DeepFOS metadata:

```json
{
  "DEEPCUBE_MCP_TRANSPORT": "stdio",
  "DEEPCUBE_MCP_ENV_URL": "https://test.deepfos.com/deepfos-server/",
  "DEEPCUBE_MCP_HEADERS": "{\"app\":\"demo\",\"space\":\"demo\",\"token\":\"...\"}",
  "DEEPCUBE_MCP_DIM_INIT_MAX_CONCURRENCY": "4"
}
```

Subprocesses are kept alive per chat session when `DEEP_AGENT_MCP_STDIO_SESSION_SCOPE=true` (default).
