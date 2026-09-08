"""MCP server configuration manager for harness-specific formats."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class MCPServerManager:
    """Generate MCP server configurations in harness-specific formats.

    Each harness has different configuration requirements:
    - Goose: YAML format in ~/.config/goose/config.yaml
    - Pi: JSON in PI_CODING_AGENT_DIR/mcp_servers.json
    - OpenCode: JSON embedded in OPENCODE_CONFIG_CONTENT
    - Claude Code: JSON in ~/.claude/mcp_servers.json
    """

    def generate_config(self, servers: list[dict], harness_name: str) -> dict | str:
        """Generate harness-specific MCP config from generic server list.

        Args:
            servers: List of MCP server configurations from harnesses.yaml
            harness_name: Name of the harness (goose, pi, opencode, claude-code)

        Returns:
            Harness-specific configuration structure
        """
        if harness_name == "goose":
            return self._generate_goose_config(servers)
        elif harness_name == "pi":
            return self._generate_pi_config(servers)
        elif harness_name == "opencode":
            return self._generate_opencode_config(servers)
        elif harness_name == "claude-code":
            return self._generate_claude_code_config(servers)
        else:
            raise ValueError(f"Unknown harness: {harness_name}")

    def _generate_goose_config(self, servers: list[dict]) -> dict:
        """Generate Goose extensions config.

        Goose config format (YAML):
        extensions:
          filesystem:
            type: stdio
            enabled: true
            cmd: npx
            args: ["-y", "@modelcontextprotocol/server-filesystem", "/testbed"]
            timeout: 300

        Args:
            servers: List of MCP server configs

        Returns:
            Dict with 'extensions' key containing per-server config
        """
        extensions = {}

        for server in servers:
            server_name = server.get("name")
            if not server_name:
                logger.warning("MCP server missing 'name' field, skipping")
                continue

            extensions[server_name] = {
                "type": "stdio",
                "enabled": True,
                "cmd": server.get("command"),
                "args": server.get("args", []),
                "env": server.get("env", {}),
                "timeout": server.get("timeout", 300),
            }

        return {"extensions": extensions}

    def _generate_pi_config(self, servers: list[dict]) -> dict:
        """Generate Pi MCP servers config.

        Pi config format (JSON):
        {
          "mcpServers": {
            "memory": {
              "command": "npx",
              "args": ["-y", "@modelcontextprotocol/server-memory"],
              "env": {}
            }
          }
        }

        Args:
            servers: List of MCP server configs

        Returns:
            Dict with 'mcpServers' key containing per-server config
        """
        mcp_servers = {}

        for server in servers:
            server_name = server.get("name")
            if not server_name:
                logger.warning("MCP server missing 'name' field, skipping")
                continue

            mcp_servers[server_name] = {
                "command": server.get("command"),
                "args": server.get("args", []),
            }

            # Add env if present
            env = server.get("env", {})
            if env:
                mcp_servers[server_name]["env"] = env

        return {"mcpServers": mcp_servers}

    def _generate_opencode_config(self, servers: list[dict]) -> dict:
        """Generate OpenCode MCP servers config.

        OpenCode embeds MCP config in OPENCODE_CONFIG_CONTENT JSON:
        {
          "provider": {...},
          "mcpServers": {
            "memory": {
              "command": "npx",
              "args": ["-y", "@modelcontextprotocol/server-memory"]
            }
          }
        }

        Args:
            servers: List of MCP server configs

        Returns:
            Dict with 'mcpServers' key (merged into OPENCODE_CONFIG_CONTENT)
        """
        mcp_servers = {}

        for server in servers:
            server_name = server.get("name")
            if not server_name:
                logger.warning("MCP server missing 'name' field, skipping")
                continue

            mcp_servers[server_name] = {
                "command": server.get("command"),
                "args": server.get("args", []),
            }

            # Add env if present
            env = server.get("env", {})
            if env:
                mcp_servers[server_name]["env"] = env

        return {"mcpServers": mcp_servers}

    def _generate_claude_code_config(self, servers: list[dict]) -> dict:
        """Generate Claude Code MCP servers config.

        Claude Code config format (JSON, ~/.claude/mcp_servers.json):
        {
          "mcpServers": {
            "memory": {
              "command": "npx",
              "args": ["-y", "@modelcontextprotocol/server-memory"]
            }
          }
        }

        Args:
            servers: List of MCP server configs

        Returns:
            Dict with 'mcpServers' key for Claude Desktop format
        """
        mcp_servers = {}

        for server in servers:
            server_name = server.get("name")
            if not server_name:
                logger.warning("MCP server missing 'name' field, skipping")
                continue

            mcp_servers[server_name] = {
                "command": server.get("command"),
                "args": server.get("args", []),
            }

            # Add env if present
            env = server.get("env", {})
            if env:
                mcp_servers[server_name]["env"] = env

        return {"mcpServers": mcp_servers}
