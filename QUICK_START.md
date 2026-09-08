# Skills & MCP Quick Start Guide

## Installation & Configuration

### Basic Setup

Add to your run configuration:

```yaml
benchmark: swebench-lite
harness: goose                  # or pi, opencode, claude-code
limit: 1

overrides:
  harness:
    skills:
      - name: rgctl
        source_type: github_release
        source_url: https://github.com/sshaaf/rgctl/releases
        version: latest
        binary_pattern: "rgctl-{version}-{arch}.tar.gz"
        binary_install_path: /usr/local/bin/rgctl
        skill_md_url: https://github.com/sshaaf/rgctl/raw/main/AGENTS.md
        required: true

    mcp_servers:
      - name: memory
        command: npx
        args: ["-y", "@modelcontextprotocol/server-memory"]
```

### Run Tests

```bash
# Goose with rgctl skill
python3 -m acb run --config config/swebench-lite/run.goose-with-rgctl.yaml

# Pi with MCP
python3 -m acb run --config config/swebench-lite/run.pi-with-mcp.yaml

# OpenCode with MCP
python3 -m acb run --config config/swebench-lite/run.opencode-with-mcp.yaml

# Claude Code with MCP
python3 -m acb run --config config/swebench-lite/run.claude-code-with-mcp.yaml
```

## Configuration Reference

### Skills Section

```yaml
skills:
  - name: tool-name
    source_type: github_release           # github_release, git, or local
    source_url: https://github.com/...    # URL to source
    version: latest                       # Version or "latest" (auto-resolved)
    binary_pattern: "tool-{version}-{arch}.tar.gz"  # Download pattern
    binary_install_path: /usr/local/bin/tool        # Install location
    binary_name: tool                     # Optional: explicit binary name
    skill_md_url: https://...             # Optional: URL to SKILL.md/AGENTS.md
    post_install: []                      # Optional: post-install hooks
    required: true                        # Fail run if skill fails?
```

### MCP Servers Section

```yaml
mcp_servers:
  - name: server-name
    command: npx
    args:
      - "-y"
      - "@modelcontextprotocol/server-memory"
    env: {}                               # Optional: environment variables
    timeout: 300                          # Optional: timeout in seconds
```

## Troubleshooting

### Skill Installation Failed

Check logs in `runs/<run_id>/acb.log`:

```bash
tail -50 runs/your-run-id/acb.log
```

Look for:
- `HTTP Error 404` → Binary not found for architecture (fallback will try x86_64)
- `No executable found` → Tarball extraction didn't produce binary
- `Multiple executables` → Add `binary_name` to disambiguate

### MCP Server Not Starting

Check container logs:

```bash
podman logs <container_id>
```

### Cache Issues

Clear and retry:

```bash
rm -rf runs/.cache/skills/your-skill-name*
python3 -m acb run --config your-config.yaml
```

## How It Works

### Skills Flow

1. ✅ Resolve `version: latest` via GitHub API
2. ✅ Download binary matching `binary_pattern`
3. ✅ Extract tarball if compressed
4. ✅ Auto-detect executable or use `binary_name`
5. ✅ Install to `binary_install_path`
6. ✅ Copy SKILL.md to harness skill directory
7. ✅ Run post-install hooks if configured

### MCP Flow

1. ✅ Read `mcp_servers` from config
2. ✅ Generate harness-specific config
3. ✅ Write to harness config location:
   - Goose: `~/.config/goose/config.yaml`
   - Pi: `$PI_CODING_AGENT_DIR/mcp_servers.json`
   - OpenCode: `OPENCODE_CONFIG_CONTENT` env var
   - Claude Code: `~/.claude/mcp_servers.json`

## Common Patterns

### GitHub Release Download

```yaml
source_type: github_release
source_url: https://github.com/owner/repo/releases
version: latest
binary_pattern: "app-{version}-{arch}.tar.gz"
binary_install_path: /usr/local/bin/app
```

### Git Repository

```yaml
source_type: git
source_url: https://github.com/owner/repo.git
# Copies entire repo to ~/.agents/skills/name/
```

### Local Directory

```yaml
source_type: local
source_path: /path/to/local/skill
```

## Performance Tips

- Cache is at `runs/.cache/skills/`
- Version cache expires after 1 hour
- Subsequent runs reuse cached skills
- First run: ~30 seconds
- Cached run: <1 second
- Same-hour API calls: no extra requests

## Debugging

Enable verbose logging:

```bash
export ACB_DEBUG=1
python3 -m acb run --config your-config.yaml 2>&1 | tee debug.log
```

Check what was cached:

```bash
ls -la runs/.cache/
ls -la runs/.cache/skills/
cat runs/.cache/.github_versions.json
```

## Architecture Support

| Architecture | Status |
|--------------|--------|
| arm64 (native) | Tries aarch64, falls back to x86_64 |
| x86_64 (native) | Tries x86_64 |
| arm64 (container) | Emulated x86_64 via Rosetta |
| x86_64 (container) | Native |

---

**For detailed documentation, see:**
- `SKILLS_AND_MCP_PLAN.md` - Architecture overview
- `SKILLS_IMPLEMENTATION_FIXES.md` - Implementation details
- `PHASE3_COMPLETION.md` - Multi-harness support
