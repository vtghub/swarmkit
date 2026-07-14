"""swarmkit as an MCP client: connect to a real stdio MCP server (a tiny
fixture server here — any protocol-compliant server works the same way; this
was also verified by hand against the real vtghub/mcp-native-core binary
while building client_tools.py) and call its tools both via the raw
diagnostic listing and via the Anthropic-wrapped tool_runner adapter, all
without touching a live LLM.
"""

from __future__ import annotations

import sys
from pathlib import Path

from swarmkit.mcp_server.client_tools import connect_stdio, list_tools

FIXTURE_SERVER = str(Path(__file__).parent.parent / "fixtures" / "echo_mcp_server.py")


async def test_list_tools_returns_real_schema():
    tools = await list_tools(sys.executable, [FIXTURE_SERVER])
    names = {t["name"] for t in tools}
    assert {"echo", "add"} <= names
    echo_tool = next(t for t in tools if t["name"] == "echo")
    assert echo_tool["input_schema"]["required"] == ["text"]


async def test_connect_stdio_wraps_tools_that_actually_call_the_server():
    async with connect_stdio(sys.executable, [FIXTURE_SERVER]) as tools:
        names = {t.name for t in tools}
        assert {"echo", "add"} <= names

        add_tool = next(t for t in tools if t.name == "add")
        result = await add_tool.call({"a": 2, "b": 3})
        assert result == [{"type": "text", "text": "5"}]
