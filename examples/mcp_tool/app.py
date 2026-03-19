"""Call an MCP tool via ClientSession and return the result."""

from mcp.client.session import ClientSession


async def fetch_weather(session: ClientSession, city: str) -> dict:
    """Call the 'get_weather' MCP tool and return its result."""
    result = await session.call_tool("get_weather", {"city": city})
    return result
