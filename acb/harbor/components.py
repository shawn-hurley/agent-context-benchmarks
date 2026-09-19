"""Compose task capabilities with explicitly selected ACB capabilities."""
from copy import deepcopy

from acb.mcp import MCPServerManager


def with_task_mcp(config: dict, task_servers: list, harness: str) -> dict:
    """Preserve task servers and reject ambiguous names or unsupported transports.

    Harbor resolves task placeholders before passing these servers to the agent.
    Keep those resolved values intact and do not mutate the prepared plan.
    """
    result = deepcopy(config)
    servers = result.get("mcp_servers", []) + [
        server.model_dump(exclude_none=True) for server in task_servers
    ]
    MCPServerManager().generate_config(servers, harness)
    if servers:
        result["mcp_servers"] = servers
    return result
