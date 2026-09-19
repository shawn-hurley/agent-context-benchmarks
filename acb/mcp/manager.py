"""MCP server configuration manager for harness-specific formats."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class MCPServerManager:
    """Generate MCP server configurations in harness-specific formats.

    Each harness has different configuration requirements:
    - Goose: YAML format in ~/.config/goose/config.yaml
    - Pi: reserved JSON format; runtime delivery requires an extension
    - OpenCode: JSON embedded in OPENCODE_CONFIG_CONTENT
    - Claude Code: JSON explicitly passed with --mcp-config
    """

    def generate_config(self, servers: list[dict], harness_name: str) -> dict | str:
        """Generate harness-specific MCP config from generic server list.

        Args:
            servers: List of MCP server configurations from harnesses.yaml
            harness_name: Name of the harness (goose, pi, opencode, claude-code)

        Returns:
            Harness-specific configuration structure
        """
        seen = set()
        for server in servers:
            if not isinstance(server, dict):
                raise ValueError("MCP servers must be mappings")
            name = server.get('name')
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', name) or name in seen:
                raise ValueError(f'MCP server name must be unique and contain only letters, digits, underscore or hyphen: {name!r}')
            seen.add(name)
            if server.get('transport', 'stdio') != 'stdio' or server.get('url'):
                raise ValueError(f'MCP server {name}: only stdio transport is currently supported by ACB adapters')
            if not isinstance(server.get('command'), str) or not server['command']:
                raise ValueError(f'MCP server {name}: command is required')
            if not isinstance(server.get('args', []), list) or any(not isinstance(arg, str) for arg in server.get('args', [])):
                raise ValueError(f'MCP server {name}: args must be strings')
            if not isinstance(server.get('env', {}), dict) or any(not isinstance(k, str) or not isinstance(v, str) for k,v in server.get('env', {}).items()):
                raise ValueError(f'MCP server {name}: env must map strings to strings')
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
        """Generate OpenCode's native `mcp` map for final config composition."""
        mcp_servers = {}

        for server in servers:
            server_name = server.get("name")
            if not server_name:
                logger.warning("MCP server missing 'name' field, skipping")
                continue

            command = server.get("command")
            if not isinstance(command, str) or not command:
                raise ValueError(f"OpenCode MCP server {server_name!r} requires a command")
            mcp_servers[server_name] = {"type": "local", "command": [command, *server.get("args", [])]}

            # Add env if present
            env = server.get("env", {})
            if env:
                mcp_servers[server_name]["environment"] = env

        return {"mcp": mcp_servers}

    def _generate_claude_code_config(self, servers: list[dict]) -> dict:
        """Generate Claude Code MCP servers config.

        Claude Code config format (JSON passed with --mcp-config):
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
