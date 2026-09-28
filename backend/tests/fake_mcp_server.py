"""A tiny stdio MCP server for the hub tests."""
import asyncio
import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fake")


@mcp.tool()
def echo(text: str) -> str:
    return text


@mcp.tool()
def fail() -> str:
    raise ValueError("nope")


@mcp.tool()
async def slow() -> str:
    await asyncio.sleep(30)
    return "late"


@mcp.tool()
def pid() -> str:
    return str(os.getpid())


@mcp.tool()
def secret() -> str:
    return "never exposed"


if __name__ == "__main__":
    mcp.run()
