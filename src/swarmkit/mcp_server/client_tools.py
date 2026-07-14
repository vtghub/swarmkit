"""swarmkit as an MCP *client*: connect to an external MCP server (stdio
transport) and expose its tools as ready-to-use Anthropic tool_runner tools.

This is the integration point for third-party MCP servers — including the
user's own `vtghub/mcp-native-core` (a real Rust stdio MCP server exposing
`fast_search`/`parse_structure`, verified against the actual binary while
building this module) — with no swarmkit-side special-casing: any
protocol-compliant stdio MCP server works through this same path.

Uses the official `mcp` Python SDK for the protocol and the Anthropic SDK's
own MCP conversion helpers (`anthropic.lib.tools.mcp`) for the tool_runner
adapter — no protocol reimplementation here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from anthropic.lib.tools import BetaAsyncFunctionTool
from anthropic.lib.tools.mcp import async_mcp_tool
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


@asynccontextmanager
async def connect_stdio(
    command: str, args: list[str] | None = None, env: dict[str, str] | None = None
) -> AsyncIterator[list[BetaAsyncFunctionTool[Any]]]:
    """Spawn `command` as a stdio MCP server, initialize the session, and
    yield its tools already wrapped as Anthropic tool_runner-compatible
    tools. The subprocess and session stay alive for the duration of the
    `with` block — real, held-open MCP protocol usage, not a one-shot probe.

    `env`, when omitted, means the child only inherits a curated safe subset
    of variables (the `mcp` SDK's default) — notably NOT things like
    `ANTHROPIC_API_KEY` or `SWARMKIT_RUNTIME_DIR`. Pass `env=dict(os.environ)`
    to fully inherit the parent's environment for a trusted local subprocess
    (e.g. swarmkit's own server); leave it default for an arbitrary
    third-party server you don't want handed your whole environment.

    Usage:
        async with connect_stdio("mcp-native-core") as tools:
            result = await agent.run(goal, ..., extra_tools=tools)
    """
    params = StdioServerParameters(command=command, args=args or [], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            yield [async_mcp_tool(t, session) for t in tools_result.tools]


async def list_tools(
    command: str, args: list[str] | None = None, env: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """List a stdio MCP server's tools (name, description, input schema)
    without wrapping them for the tool runner — a lightweight diagnostic,
    e.g. for `swarmkit mcp list-tools`. See `connect_stdio` for `env` semantics."""
    params = StdioServerParameters(command=command, args=args or [], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return [
                {"name": t.name, "description": t.description, "input_schema": t.inputSchema}
                for t in result.tools
            ]
