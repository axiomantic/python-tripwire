"""Test MCP tool call using tripwire mcp_mock."""

import pytest

import tripwire

from .app import fetch_weather


@pytest.mark.asyncio
async def test_fetch_weather():
    from mcp.client.session import ClientSession

    tripwire.mcp_mock.mock_call_tool(
        "get_weather",
        returns={"content": [{"type": "text", "text": "Sunny, 72F"}]},
    )

    with tripwire:
        session = object.__new__(ClientSession)
        result = await fetch_weather(session, "San Francisco")

    assert result == {"content": [{"type": "text", "text": "Sunny, 72F"}]}

    tripwire.mcp_mock.assert_call_tool(
        "get_weather",
        arguments={"city": "San Francisco"},
        direction="client",
    )
