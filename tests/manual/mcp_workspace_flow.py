"""Exercise the real Streamable HTTP MCP Workspace-to-Sandbox flow."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx2
from dotenv import load_dotenv
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MCP_URL = os.getenv("WORKSPACE_GATEWAY_MCP_URL", "http://127.0.0.1:8080/mcp")


def structured(result: Any) -> Any:
    if result.is_error:
        messages = [getattr(item, "text", str(item)) for item in result.content]
        raise RuntimeError("; ".join(messages))
    if result.structured_content is None:
        raise RuntimeError("MCP tool returned no structured content")
    return result.structured_content


async def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    api_key = os.getenv("GATEWAY_API_KEY", "").strip()
    existing_workspace_id = os.getenv("MCP_WORKSPACE_ID", "").strip()
    existing_sandbox_id = os.getenv("MCP_SANDBOX_ID", "").strip()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    node_source = """\
const project = {
  name: 'MCP Workspace Sandbox Demo',
  runtime: process.version,
  message: 'Code was written to Workspace and executed inside PAI Sandbox'
};

console.log(JSON.stringify(project));
"""
    package_source = """\
{
  "name": "mcp-workspace-sandbox-demo",
  "version": "1.0.0",
  "private": true,
  "scripts": {
    "start": "node index.js"
  }
}
"""
    async with httpx2.AsyncClient(headers=headers) as http_client:
        transport = streamable_http_client(MCP_URL, http_client=http_client)
        async with Client(
            transport,
            mode="legacy",
            read_timeout_seconds=600,
            cache=None,
        ) as client:
            tool_list = await client.list_tools()
            names = {tool.name for tool in tool_list.tools}
            required = {
                "workspace_create",
                "workspace_write_file",
                "workspace_commit",
                "workspace_read_file",
                "workspace_history",
                "workspace_run",
                "sandbox_get",
            }
            missing = sorted(required - names)
            if missing:
                raise RuntimeError(f"MCP server is missing required tools: {missing}")

            if existing_workspace_id:
                workspace = structured(
                    await client.call_tool(
                        "workspace_get", {"workspace_id": existing_workspace_id}
                    )
                )
            else:
                workspace = structured(
                    await client.call_tool(
                        "workspace_create",
                        {
                            "name": "MCP Workspace Sandbox Demo",
                            "description": "Real MCP end-to-end verification",
                        },
                    )
                )
            workspace_id = workspace["id"]

            for path, text in (
                ("index.js", node_source),
                ("package.json", package_source),
            ):
                structured(
                    await client.call_tool(
                        "workspace_write_file",
                        {
                            "workspace_id": workspace_id,
                            "path": path,
                            "text": text,
                        },
                    )
                )

            commit = structured(
                await client.call_tool(
                    "workspace_commit",
                    {
                        "workspace_id": workspace_id,
                        "message": "Add MCP Workspace Node.js demo",
                    },
                )
            )
            version = commit["version"]["version"]

            committed_file = structured(
                await client.call_tool(
                    "workspace_read_file",
                    {
                        "workspace_id": workspace_id,
                        "path": "index.js",
                        "version": version,
                    },
                )
            )
            if "executed inside PAI Sandbox" not in committed_file["content"]:
                raise RuntimeError("Committed Workspace file content did not match")

            run = structured(
                await client.call_tool(
                    "workspace_run",
                    {
                        "workspace_id": workspace_id,
                        "version": version,
                        "sandbox_id": existing_sandbox_id or None,
                        "command": "npm start",
                        "auto_commit": False,
                        "timeout_seconds": 300,
                    },
                )
            )
            if run["result"]["exit_code"] != 0:
                raise RuntimeError(f"Sandbox command failed: {run['result']}")
            if "executed inside PAI Sandbox" not in run["result"]["stdout"]:
                raise RuntimeError("Sandbox output did not contain the expected marker")

            sandbox = structured(
                await client.call_tool(
                    "sandbox_get",
                    {"gateway_id": run["sandbox"]["id"], "refresh": True},
                )
            )
            history = structured(
                await client.call_tool(
                    "workspace_history",
                    {"workspace_id": workspace_id, "limit": 5},
                )
            )
            history_items = history.get("result", history) if isinstance(history, dict) else history

            print(
                json.dumps(
                    {
                        "mcp_url": MCP_URL,
                        "tools_available": len(names),
                        "workspace_id": workspace_id,
                        "version": version,
                        "run_id": run["run_id"],
                        "sandbox_id": sandbox["id"],
                        "provider_sandbox_id": sandbox["provider_sandbox_id"],
                        "sandbox_state": sandbox["state"],
                        "created_sandbox": run["created_sandbox"],
                        "exit_code": run["result"]["exit_code"],
                        "stdout": run["result"]["stdout"].strip(),
                        "history_count": len(history_items),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )


if __name__ == "__main__":
    asyncio.run(main())
