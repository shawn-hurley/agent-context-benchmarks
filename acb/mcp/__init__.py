"""MCP (Model Context Protocol) server configuration for ACB harnesses.

Each harness uses a different configuration format for MCP servers:
- Goose: ~/.config/goose/config.yaml (extensions section)
- Pi: PI_CODING_AGENT_DIR/mcp_servers.json
- OpenCode: OPENCODE_CONFIG_CONTENT JSON (mcpServers key)
- Claude Code: ~/.claude/mcp_servers.json
"""

from .manager import MCPServerManager

__all__ = ["MCPServerManager"]
