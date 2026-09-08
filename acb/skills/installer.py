"""Skill installer following agentskills.io standard.

Skills are installed to harness-specific directories and discovered automatically
by each harness at startup. Skills consist of a directory with a required SKILL.md
file containing YAML frontmatter (name, description, etc.) and optional supporting
files (scripts, references, assets).
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from acb.container import container_cp_in, container_exec_capture
from acb.harnesses._cache import binary_cache_lock

if TYPE_CHECKING:
    from acb.ui import ProgressTracker


logger = logging.getLogger(__name__)

# Lock to ensure thread-safe downloads/fetches
_FETCH_LOCK = threading.Lock()


@dataclass
class SkillResult:
    """Result of skill installation."""

    success: bool
    skill_name: str
    skill_path: Path | None
    container_path: str | None
    error: str | None = None
    logs: list[str] = None

    def __post_init__(self):
        if self.logs is None:
            self.logs = []


class SkillInstaller:
    """Install skills to harness-specific directories following agentskills.io standard."""

    # Harness-specific skill directory locations
    HARNESS_SKILL_PATHS = {
        "goose": "~/.agents/skills",
        "pi": "~/.pi/agent/skills",
        "opencode": "~/.config/opencode/skills",
        "claude-code": "~/.claude/skills",
    }

    def __init__(self, cache_dir: Path):
        """Initialize with cache directory for downloaded skills.

        Args:
            cache_dir: Directory to cache downloaded skills (runs/.cache/skills/)
        """
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def install_skill(
        self,
        skill_config: dict,
        container: str,
        arch: str,
        harness_name: str,
        tracker: ProgressTracker | None = None,
        tracker_key: str | None = None,
    ) -> SkillResult:
        """Install a skill to container at harness-specific path.

        Args:
            skill_config: Skill configuration dict from harnesses.yaml
            container: Podman container ID
            arch: Target architecture (arm64 or amd64)
            harness_name: Name of harness (goose, pi, opencode, claude-code)
            tracker: Optional progress tracker
            tracker_key: Optional tracker key for activity updates

        Returns:
            SkillResult with success status and details
        """
        skill_name = skill_config.get("name")
        if not skill_name:
            return SkillResult(
                success=False,
                skill_name="<unknown>",
                skill_path=None,
                container_path=None,
                error="Skill config missing 'name' field",
            )

        logs = []

        try:
            # Step 1: Determine where to install in container
            container_base_path = self.HARNESS_SKILL_PATHS.get(
                harness_name, "~/.agents/skills"
            )
            container_skill_path = f"{container_base_path}/{skill_name}"

            logs.append(f"Installing skill '{skill_name}' for {harness_name}")
            logs.append(f"Install path: {container_skill_path}")

            if tracker and tracker_key:
                tracker.update_activity(
                    tracker_key, f"skills: fetching {skill_name} ({arch})"
                )

            # Step 2: Fetch skill content (get local path)
            local_skill_path = self._fetch_skill(
                skill_config, arch, tracker, tracker_key
            )
            logs.append(f"Fetched to: {local_skill_path}")

            # Step 3: Validate SKILL.md
            skill_md_path = local_skill_path / "SKILL.md"
            if not skill_md_path.exists():
                return SkillResult(
                    success=False,
                    skill_name=skill_name,
                    skill_path=local_skill_path,
                    container_path=None,
                    error=f"SKILL.md not found at {skill_md_path}",
                    logs=logs,
                )

            # Validate frontmatter
            validation_error = self._validate_skill_md(skill_md_path)
            if validation_error:
                return SkillResult(
                    success=False,
                    skill_name=skill_name,
                    skill_path=local_skill_path,
                    container_path=None,
                    error=validation_error,
                    logs=logs,
                )

            logs.append(f"SKILL.md validated successfully")

            if tracker and tracker_key:
                tracker.update_activity(tracker_key, f"skills: installing {skill_name}")

            # Step 4: Create skill directory in container and copy files
            container_exec_capture(
                container,
                ["mkdir", "-p", container_base_path.replace("~/", "/root/")],
            )

            # Copy entire skill directory
            skill_container_dir = container_skill_path.replace("~/", "/root/")
            container_exec_capture(container, ["mkdir", "-p", skill_container_dir])

            # Copy all files from local skill directory
            for file_path in local_skill_path.rglob("*"):
                if file_path.is_file():
                    rel_path = file_path.relative_to(local_skill_path)
                    target_path = Path(skill_container_dir) / rel_path
                    target_dir = target_path.parent
                    container_exec_capture(
                        container, ["mkdir", "-p", str(target_dir)]
                    )
                    container_cp_in(container, file_path, str(target_path))

            logs.append(f"Skill files copied to {skill_container_dir}")

            # Step 5: Install binary to specified path if configured
            binary_install_path = skill_config.get("binary_install_path")
            if binary_install_path:
                if tracker and tracker_key:
                    tracker.update_activity(
                        tracker_key, f"skills: installing binary for {skill_name}"
                    )

                binary_name = skill_config.get("binary_name")
                try:
                    self._install_binary_to_path(
                        container,
                        local_skill_path,
                        binary_install_path,
                        binary_name,
                        skill_name,
                    )
                    logs.append(f"Binary installed to {binary_install_path}")
                except Exception as e:
                    error_msg = f"Failed to install binary: {str(e)}"
                    logs.append(error_msg)
                    logger.error(error_msg)

                    # Only fail if skill is required
                    if skill_config.get("required", False):
                        return SkillResult(
                            success=False,
                            skill_name=skill_name,
                            skill_path=local_skill_path,
                            container_path=skill_container_dir,
                            error=error_msg,
                            logs=logs,
                        )
                    # For optional skills, log warning and continue
                    logger.warning(f"Binary installation failed for optional skill '{skill_name}', continuing...")

            # Step 6: Run post_install hooks if configured
            post_install = skill_config.get("post_install", [])
            if post_install:
                if tracker and tracker_key:
                    tracker.update_activity(
                        tracker_key, f"skills: running post-install hooks for {skill_name}"
                    )

                for hook in post_install:
                    hook_result = self._run_post_install_hook(
                        container, skill_name, hook, logs
                    )
                    if not hook_result["success"]:
                        on_failure = hook.get("on_failure", "fail")
                        error_msg = f"Post-install hook failed: {hook_result['error']}"
                        logs.append(error_msg)

                        if on_failure == "fail":
                            return SkillResult(
                                success=False,
                                skill_name=skill_name,
                                skill_path=local_skill_path,
                                container_path=skill_container_dir,
                                error=error_msg,
                                logs=logs,
                            )
                        elif on_failure == "warn":
                            logger.warning(error_msg)

            logs.append(f"Skill '{skill_name}' installed successfully")

            return SkillResult(
                success=True,
                skill_name=skill_name,
                skill_path=local_skill_path,
                container_path=skill_container_dir,
                logs=logs,
            )

        except Exception as e:
            error = f"Unexpected error installing skill: {str(e)}"
            logs.append(error)
            logger.exception(error)
            return SkillResult(
                success=False,
                skill_name=skill_name,
                skill_path=None,
                container_path=None,
                error=error,
                logs=logs,
            )

    def _fetch_skill(
        self,
        skill_config: dict,
        arch: str,
        tracker: ProgressTracker | None = None,
        tracker_key: str | None = None,
    ) -> Path:
        """Fetch skill content to local cache.

        Args:
            skill_config: Skill configuration
            arch: Target architecture
            tracker: Optional progress tracker
            tracker_key: Optional tracker key

        Returns:
            Path to local skill directory

        Raises:
            ValueError: If source_type is unsupported or fetch fails
        """
        source_type = skill_config.get("source_type", "github_release")
        skill_name = skill_config.get("name")

        if source_type == "github_release":
            return self._fetch_github_release(
                skill_config, arch, tracker, tracker_key
            )
        elif source_type == "git":
            return self._fetch_git_repo(skill_config, tracker, tracker_key)
        elif source_type == "local":
            return self._fetch_local(skill_config)
        else:
            raise ValueError(f"Unsupported source_type: {source_type}")

    def _fetch_github_release(
        self,
        skill_config: dict,
        arch: str,
        tracker: ProgressTracker | None = None,
        tracker_key: str | None = None,
    ) -> Path:
        """Fetch skill binary and SKILL.md from GitHub releases.

        Implemented in fetchers module for rgctl-specific logic.
        This is a placeholder that delegates to the fetcher.
        """
        from acb.skills import fetchers

        skill_name = skill_config.get("name")
        version = skill_config.get("version", "latest")

        # Use thread-safe locking for downloads
        cache_key = f"{skill_name}-{version}-{arch}"

        with _FETCH_LOCK, binary_cache_lock(self.cache_dir, cache_key):
            return fetchers.fetch_github_release(
                skill_config, self.cache_dir, arch
            )

    def _fetch_git_repo(
        self,
        skill_config: dict,
        tracker: ProgressTracker | None = None,
        tracker_key: str | None = None,
    ) -> Path:
        """Fetch skill from git repository."""
        from acb.skills import fetchers

        skill_name = skill_config.get("name")
        cache_key = f"{skill_name}-git"

        with _FETCH_LOCK, binary_cache_lock(self.cache_dir, cache_key):
            return fetchers.fetch_git_repo(skill_config, self.cache_dir)

    def _fetch_local(self, skill_config: dict) -> Path:
        """Use existing local skill directory."""
        from acb.skills import fetchers

        return fetchers.fetch_local(skill_config)

    def _validate_skill_md(self, skill_md_path: Path) -> str | None:
        """Validate SKILL.md frontmatter and structure.

        Args:
            skill_md_path: Path to SKILL.md file

        Returns:
            Error message if invalid, None if valid
        """
        from acb.skills import validator

        return validator.validate_skill_md(skill_md_path)

    def _install_binary_to_path(
        self,
        container: str,
        skill_dir: Path,
        binary_install_path: str,
        binary_name: str | None = None,
        skill_name: str | None = None,
    ) -> None:
        """Install binary from skill directory to specified path in container.

        Supports:
        - Auto-detection: single executable in directory
        - Name matching: matches binary to skill name if multiple executables
        - Explicit: use binary_name if provided

        Args:
            container: Podman container ID
            skill_dir: Local skill directory containing binary
            binary_install_path: Target path in container (e.g., /usr/local/bin/rgctl)
            binary_name: Optional specific binary filename to install
            skill_name: Skill name for name-matching heuristic

        Raises:
            RuntimeError: If binary not found or installation fails
        """
        # Find all executable files in skill directory
        executables = [
            f for f in skill_dir.rglob("*")
            if f.is_file() and os.access(f, os.X_OK)
        ]

        logger.debug(f"Found {len(executables)} executable(s) in {skill_dir}")

        # Determine which binary to install
        if binary_name:
            # Explicit binary name specified
            binary_path = skill_dir / binary_name
            if not binary_path.exists():
                raise RuntimeError(
                    f"Specified binary '{binary_name}' not found in {skill_dir}"
                )
            if not os.access(binary_path, os.X_OK):
                raise RuntimeError(
                    f"Specified binary '{binary_name}' is not executable"
                )
            logger.info(f"Using explicitly specified binary: {binary_name}")

        elif len(executables) == 1:
            # Single executable - use it automatically
            binary_path = executables[0]
            logger.info(
                f"Single executable found, auto-selected: {binary_path.name}"
            )

        elif len(executables) > 1:
            # Multiple executables - try skill name matching
            if skill_name:
                matches = [
                    e for e in executables
                    if skill_name.lower() in e.name.lower()
                ]
                if len(matches) == 1:
                    binary_path = matches[0]
                    logger.info(
                        f"Multiple executables found, selected by name match: {binary_path.name}"
                    )
                elif len(matches) > 1:
                    raise RuntimeError(
                        f"Multiple executables match skill name '{skill_name}': {[e.name for e in matches]}. "
                        f"Specify 'binary_name' to disambiguate."
                    )
                else:
                    raise RuntimeError(
                        f"Multiple executables found in skill directory, none match skill name '{skill_name}': "
                        f"{[e.name for e in executables]}. Specify 'binary_name' in configuration."
                    )
            else:
                raise RuntimeError(
                    f"Multiple executables found, skill name not provided for matching: "
                    f"{[e.name for e in executables]}. Specify 'binary_name' in configuration."
                )

        else:
            # No executables found
            raise RuntimeError(
                f"No executable files found in {skill_dir}. "
                f"Ensure binary_pattern correctly downloads the binary and tarball extraction works."
            )

        # Install binary to container
        logger.debug(f"Installing {binary_path.name} to {binary_install_path}")

        # Ensure target directory exists in container
        target_dir = str(Path(binary_install_path).parent)
        container_exec_capture(container, ["mkdir", "-p", target_dir])

        # Copy binary to container
        container_path = binary_install_path.replace("~/", "/root/")
        container_cp_in(container, binary_path, container_path)

        # Make executable in container
        container_exec_capture(container, ["chmod", "+x", container_path])

        logger.info(f"Binary installed to {container_path}")

    def _run_post_install_hook(
        self, container: str, skill_name: str, hook: dict, logs: list[str]
    ) -> dict:
        """Execute a post-install hook in the container.

        Args:
            container: Podman container ID
            skill_name: Name of skill being installed
            hook: Hook configuration dict
            logs: List to append log messages to

        Returns:
            Dict with 'success' bool and optional 'error' string
        """
        when = hook.get("when", "always")

        # Check if we should run this hook
        if when == "never":
            logs.append(f"Post-install hook skipped (when: never)")
            return {"success": True}
        elif when == "on_first_install":
            # For now, we don't track first install, so we'll implement as 'never'
            # This can be extended later to check a marker file
            logs.append(
                f"Post-install hook skipped (when: on_first_install not yet implemented)"
            )
            return {"success": True}

        # Run the command
        command = hook.get("command", [])
        timeout = hook.get("timeout", 300)

        if not command:
            return {"success": True}

        logs.append(f"Running post-install hook: {' '.join(command)}")

        try:
            # Execute command in container
            # Note: timeout not yet supported by container_exec_capture
            result = container_exec_capture(container, command)
            logs.append(f"Post-install hook output: {result}")
            return {"success": True}
        except subprocess.TimeoutExpired:
            error = f"Post-install hook timed out after {timeout}s"
            logs.append(error)
            return {"success": False, "error": error}
        except Exception as e:
            error = f"Post-install hook failed: {str(e)}"
            logs.append(error)
            return {"success": False, "error": error}
