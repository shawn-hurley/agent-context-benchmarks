"""Skill fetchers for different source types.

Implements strategies for fetching skills from:
- GitHub releases (binaries + SKILL.md)
- Git repositories
- Local directories
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _extract_owner_repo(url: str) -> tuple[str, str]:
    """Extract owner and repo from GitHub URL.
    
    Handles formats:
    - https://github.com/owner/repo
    - https://github.com/owner/repo/releases
    - https://github.com/owner/repo/releases/latest
    
    Args:
        url: GitHub URL
        
    Returns:
        Tuple of (owner, repo)
        
    Raises:
        ValueError: If URL format not recognized
    """
    # Remove protocol and domain
    if "github.com/" not in url:
        raise ValueError(f"Not a GitHub URL: {url}")
    
    path = url.split("github.com/")[1]
    parts = path.strip("/").split("/")
    
    if len(parts) < 2:
        raise ValueError(f"Invalid GitHub URL format: {url}")
    
    return parts[0], parts[1]


def _resolve_github_latest_version(
    source_url: str, cache_file: Path
) -> str:
    """Resolve 'latest' to actual release tag via GitHub API.
    
    Caches resolved versions for 1 hour to minimize API calls.
    
    Args:
        source_url: GitHub repository URL
        cache_file: Path to cache file (runs/.cache/.github_versions.json)
        
    Returns:
        Actual tag name (e.g., "v0.4.11")
        
    Raises:
        RuntimeError: If API call fails and no cached version available
    """
    # Load cache
    cache = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text())
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse cache file {cache_file}, starting fresh")
            cache = {}
    
    cache_key = source_url
    cached_entry = cache.get(cache_key, {})
    
    # Check if cached version is still fresh (< 1 hour old)
    if cached_entry.get("version"):
        age = time.time() - cached_entry.get("timestamp", 0)
        if age < 3600:  # 1 hour
            logger.debug(
                f"Using cached version {cached_entry['version']} for {source_url} (age: {age:.0f}s)"
            )
            return cached_entry["version"]
    
    # Fetch from GitHub API
    try:
        owner, repo = _extract_owner_repo(source_url)
        api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        
        logger.info(f"Resolving 'latest' version from GitHub API: {api_url}")
        with urllib.request.urlopen(api_url) as response:  # noqa: S310
            data = json.loads(response.read().decode("utf-8"))
            version = data.get("tag_name")
            
            if not version:
                raise RuntimeError("No 'tag_name' in GitHub API response")
            
            logger.info(f"Resolved 'latest' to version: {version}")
            
            # Update cache
            cache[cache_key] = {
                "version": version,
                "timestamp": time.time(),
            }
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(cache, indent=2))
            
            return version
    except Exception as e:
        # If API fails, try to use cached version anyway
        if cached_entry.get("version"):
            logger.warning(
                f"GitHub API call failed ({e}), using cached version {cached_entry['version']}"
            )
            return cached_entry["version"]
        
        raise RuntimeError(
            f"Failed to resolve 'latest' version from GitHub API: {str(e)}"
        ) from e


def _map_architecture(arch: str) -> str:
    """Map container architecture to standard binary naming conventions.
    
    Simple 1:1 mapping for Linux container architectures:
    - arm64/aarch64 → aarch64
    - amd64/x86_64 → x86_64
    
    User specifies exact binary_pattern matching release files.
    This just maps the architecture format.
    
    Args:
        arch: Container architecture from runner (arm64, amd64, x86_64, aarch64)
        
    Returns:
        Standard architecture string for binary substitution
    """
    arch_map = {
        "arm64": "aarch64",
        "aarch64": "aarch64",
        "amd64": "x86_64",
        "x86_64": "x86_64",
    }
    # Unknown architectures returned as-is
    return arch_map.get(arch, arch)


def _fetch_release_assets(source_url: str, version: str) -> list[str]:
    """Fetch list of asset names from GitHub release.
    
    Used to provide helpful error messages when binary not found.
    
    Args:
        source_url: GitHub repository URL
        version: Release version tag (e.g., "v0.4.11")
        
    Returns:
        List of asset filenames from release, or empty list if fetch fails
    """
    try:
        owner, repo = _extract_owner_repo(source_url)
        api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{version}"
        
        with urllib.request.urlopen(api_url) as response:  # noqa: S310
            data = json.loads(response.read())
            return [asset["name"] for asset in data.get("assets", [])]
    except Exception as e:
        logger.debug(f"Failed to fetch release assets: {e}")
        return []


def _build_helpful_404_error(
    skill_name: str,
    version: str,
    source_url: str,
    binary_filename: str,
    download_url: str,
    original_arch: str,
    mapped_arch: str,
    binary_pattern: str,
) -> RuntimeError:
    """Build detailed error message when binary not found (404).
    
    Fetches available assets from GitHub and provides suggestions.
    
    Args:
        skill_name: Name of the skill
        version: Release version that was tried
        source_url: GitHub repo URL
        binary_filename: Filename that was attempted
        download_url: Full URL that was attempted
        original_arch: Original container architecture (arm64, amd64)
        mapped_arch: Mapped architecture (aarch64, x86_64)
        binary_pattern: Pattern template from config
        
    Returns:
        RuntimeError with helpful error message
    """
    # Fetch available assets for suggestions
    assets = _fetch_release_assets(source_url, version)
    
    # Build error message
    lines = [
        f"Failed to download {skill_name} binary",
        "",
        f"URL tried: {download_url}",
        f"Result: 404 Not Found",
        "",
        f"Container architecture: {original_arch}",
        f"Mapped to: {mapped_arch}",
        f"Binary pattern: {binary_pattern}",
        f"Filename: {binary_filename}",
        "",
    ]
    
    if assets:
        lines.append(f"Available assets in {version}:")
        linux_assets = []
        for asset in assets:
            if "linux" in asset.lower():
                lines.append(f"  ✓ {asset}  (Linux)")
                linux_assets.append(asset)
            else:
                lines.append(f"    {asset}")
        
        lines.extend([
            "",
            "Suggestions:",
            "  1. Update binary_pattern to match an available Linux asset",
        ])
        
        # Try to suggest a working pattern
        if linux_assets:
            example = linux_assets[0]
            # Create pattern by replacing version in example
            suggested_pattern = example.replace(version.lstrip("v"), "{version}")
            lines.append(f"  2. Example: binary_pattern: \"{suggested_pattern}\"")
        
        lines.append("  3. Or override container arch with: benchmark.image_arch: amd64")
    else:
        lines.extend([
            f"(Could not fetch asset list to show suggestions)",
            "",
            "Suggestions:",
            "  1. Check GitHub release page manually",
            f"  2. Update binary_pattern to match actual Linux asset naming",
            "  3. Or override container arch with: benchmark.image_arch: amd64",
        ])
    
    return RuntimeError("\n".join(lines))


def fetch_github_release(config: dict, cache_dir: Path, arch: str) -> Path:
    """Fetch skill binary and SKILL.md from GitHub releases.

    For skills like rgctl that distribute as GitHub releases:
    1. Download binary from release assets (user specifies exact pattern)
    2. Fetch SKILL.md (or AGENTS.md) from repo
    3. Create skill directory with both

    Args:
        config: Skill configuration with:
            - source_url: GitHub repo URL or releases base URL
            - version: Release version (or 'latest')
            - binary_pattern: Pattern for binary filename with {arch} and {version} placeholders
            - skill_md_url: Optional URL to SKILL.md or AGENTS.md
        cache_dir: Directory to cache downloaded skills
        arch: Architecture (arm64, amd64, x86_64, aarch64)

    Returns:
        Path to local skill directory

    Raises:
        RuntimeError: If download or extraction fails with helpful error message
    """
    skill_name = config.get("name")
    version = config.get("version", "latest")
    source_url = config.get("source_url")
    binary_pattern = config.get("binary_pattern")
    skill_md_url = config.get("skill_md_url")

    if not source_url:
        raise ValueError("GitHub release config missing 'source_url'")

    # Resolve "latest" to actual version tag if needed
    if version == "latest":
        version = _resolve_github_latest_version(
            source_url, cache_dir / ".github_versions.json"
        )

    # Map container architecture to standard format
    mapped_arch = _map_architecture(arch)

    # Create cache directory with resolved version
    cache_key = f"{skill_name}-{version}-{mapped_arch}"
    skill_dir = cache_dir / cache_key
    skill_dir.mkdir(parents=True, exist_ok=True)

    # Check if already cached
    skill_md_path = skill_dir / "SKILL.md"
    if skill_md_path.exists():
        logger.debug(f"Using cached skill at {skill_dir}")
        return skill_dir

    logger.info(f"Fetching {skill_name} skill from GitHub releases")

    # Download binary if pattern specified
    if binary_pattern:
        try:
            # Format pattern with version and mapped architecture
            binary_filename = binary_pattern.format(
                version=version.lstrip("v"), arch=mapped_arch
            )
            binary_path = skill_dir / binary_filename.split("/")[-1]

            if binary_path.exists():
                logger.debug(f"Binary already cached at {binary_path}")
            else:
                # Construct release URL
                if source_url.endswith("/latest"):
                    # Already has /latest
                    download_url = source_url.replace(
                        "/latest", f"/download/{version}/{binary_filename}"
                    )
                elif source_url.endswith("/releases"):
                    download_url = f"{source_url}/download/{version}/{binary_filename}"
                elif "releases" not in source_url:
                    download_url = f"{source_url}/releases/download/{version}/{binary_filename}"
                else:
                    download_url = f"{source_url}/download/{version}/{binary_filename}"

                logger.debug(f"Downloading binary from: {download_url}")
                _download_file(download_url, binary_path)
                logger.info(f"Downloaded: {binary_filename}")

        except RuntimeError as e:
            # Check if it's a 404 (not found) error - provide helpful message
            error_str = str(e)
            if "404" in error_str or "Not Found" in error_str:
                raise _build_helpful_404_error(
                    skill_name, version, source_url,
                    binary_filename, download_url,
                    arch, mapped_arch, binary_pattern
                ) from e
            else:
                # Re-raise other errors as-is
                raise

        # Check if it's a tarball that needs extraction
        if binary_path.suffix in [".gz", ".bz2", ".tar"]:
            logger.debug(f"Extracting {binary_path}")
            if binary_path.suffix == ".gz" or binary_path.name.endswith(".tar.gz"):
                with tarfile.open(binary_path) as tf:
                    tf.extractall(skill_dir)
            elif binary_path.suffix == ".bz2" or binary_path.name.endswith(".tar.bz2"):
                with tarfile.open(binary_path) as tf:
                    tf.extractall(skill_dir)
            binary_path.unlink()  # Remove archive after extraction

    # Download SKILL.md or AGENTS.md
    if skill_md_url:
        logger.debug(f"Downloading SKILL.md from: {skill_md_url}")
        skill_md_content = _fetch_text(skill_md_url)

        # If it's AGENTS.md content, convert to SKILL.md with frontmatter
        if "AGENTS" in skill_md_url or not skill_md_content.startswith("---"):
            skill_md_content = _convert_agents_md_to_skill_md(
                skill_md_content, skill_name, config
            )

        skill_md_path.write_text(skill_md_content)
        logger.debug(f"Created SKILL.md at {skill_md_path}")
    else:
        # Create minimal SKILL.md
        skill_md_content = _create_minimal_skill_md(skill_name, config)
        skill_md_path.write_text(skill_md_content)
        logger.debug(f"Created minimal SKILL.md at {skill_md_path}")

    return skill_dir


def fetch_git_repo(config: dict, cache_dir: Path) -> Path:
    """Fetch skill from git repository.

    Args:
        config: Skill configuration with:
            - source_url: Git repository URL
            - ref: Optional git ref (branch, tag, commit)
        cache_dir: Directory to cache skills

    Returns:
        Path to local skill directory

    Raises:
        RuntimeError: If git clone fails
    """
    skill_name = config.get("name")
    source_url = config.get("source_url")
    ref = config.get("ref", "main")

    if not source_url:
        raise ValueError("Git config missing 'source_url'")

    cache_key = f"{skill_name}-git"
    skill_dir = cache_dir / cache_key

    # Check if already cached
    if (skill_dir / "SKILL.md").exists():
        logger.debug(f"Using cached skill at {skill_dir}")
        return skill_dir

    logger.info(f"Cloning {skill_name} from {source_url}")

    try:
        skill_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", ref, source_url, str(skill_dir)],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"Failed to clone skill repository: {e.stderr.decode()}"
        ) from e

    return skill_dir


def fetch_local(config: dict) -> Path:
    """Use existing local skill directory.

    Args:
        config: Skill configuration with:
            - source_path: Local path to skill directory

    Returns:
        Path to local skill directory

    Raises:
        ValueError: If path doesn't exist or missing SKILL.md
    """
    source_path = config.get("source_path")

    if not source_path:
        raise ValueError("Local config missing 'source_path'")

    skill_dir = Path(source_path).expanduser().resolve()

    if not skill_dir.exists():
        raise ValueError(f"Skill directory not found: {skill_dir}")

    skill_md_path = skill_dir / "SKILL.md"
    if not skill_md_path.exists():
        raise ValueError(f"SKILL.md not found in {skill_dir}")

    logger.debug(f"Using local skill at {skill_dir}")
    return skill_dir


# Helper functions


def _download_file(url: str, dest: Path) -> None:
    """Download file from URL to destination.

    Args:
        url: URL to download from
        dest: Destination file path

    Raises:
        RuntimeError: If download fails
    """
    try:
        logger.debug(f"Downloading {url} to {dest}")
        urllib.request.urlretrieve(url, dest)  # noqa: S310
    except urllib.error.HTTPError as e:
        # Preserve HTTP error code for caller to detect 404s
        raise RuntimeError(f"HTTP Error {e.code}: {e.reason}") from e
    except Exception as e:
        raise RuntimeError(f"Failed to download {url}: {str(e)}") from e


def _fetch_text(url: str) -> str:
    """Fetch text content from URL.

    Args:
        url: URL to fetch from

    Returns:
        Text content

    Raises:
        RuntimeError: If fetch fails
    """
    try:
        logger.debug(f"Fetching {url}")
        with urllib.request.urlopen(url) as response:  # noqa: S310
            return response.read().decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"Failed to fetch {url}: {str(e)}") from e


def _convert_agents_md_to_skill_md(
    agents_md: str, skill_name: str, config: dict
) -> str:
    """Convert AGENTS.md content to SKILL.md with frontmatter.

    Args:
        agents_md: AGENTS.md content
        skill_name: Name of the skill
        config: Skill configuration

    Returns:
        SKILL.md content with frontmatter
    """
    version = config.get("version", "latest")
    source_url = config.get("source_url", "")

    # Create frontmatter
    frontmatter = f"""---
name: {skill_name}
description: {_get_skill_description(skill_name)}
license: MIT
compatibility: Requires {skill_name} binary. Run '{skill_name} discover /testbed' before first use if needed.
metadata:
  source: {source_url}
  version: {version}
---

"""

    return frontmatter + agents_md


def _create_minimal_skill_md(skill_name: str, config: dict) -> str:
    """Create minimal SKILL.md when no source provided.

    Args:
        skill_name: Name of the skill
        config: Skill configuration

    Returns:
        Minimal SKILL.md content
    """
    description = _get_skill_description(skill_name)
    version = config.get("version", "latest")

    return f"""---
name: {skill_name}
description: {description}
---

# {skill_name}

This skill provides access to {skill_name} for code analysis and queries.

## Usage

See the {skill_name} documentation for usage instructions.
"""


def _get_skill_description(skill_name: str) -> str:
    """Get default description for known skills.

    Args:
        skill_name: Name of the skill

    Returns:
        Description string
    """
    descriptions = {
        "rgctl": "Code knowledge graph for AI agents. Query repository structure, blast radius, architecture, migrations, and semantic code search. Use for understanding code relationships and impact analysis.",
    }

    return descriptions.get(
        skill_name, f"Skill providing {skill_name} functionality for code analysis."
    )
