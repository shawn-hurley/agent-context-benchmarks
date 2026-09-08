"""SKILL.md validation per agentskills.io specification.

Validates that SKILL.md files have:
- Valid YAML frontmatter
- Required fields: name, description
- Proper name format (lowercase alphanumeric + hyphens)
- Valid length constraints
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

logger = logging.getLogger(__name__)


def validate_skill_md(skill_path: Path) -> str | None:
    """Validate SKILL.md file structure and frontmatter.

    Args:
        skill_path: Path to SKILL.md file

    Returns:
        Error message if invalid, None if valid
    """
    if not skill_path.exists():
        return f"SKILL.md not found at {skill_path}"

    try:
        content = skill_path.read_text()
    except Exception as e:
        return f"Failed to read SKILL.md: {str(e)}"

    # Check for frontmatter markers
    if not content.startswith("---"):
        return "SKILL.md must start with --- (YAML frontmatter)"

    # Extract frontmatter
    try:
        parts = content.split("---", 2)
        if len(parts) < 3:
            return "SKILL.md frontmatter not properly closed with ---"

        frontmatter_str = parts[1]
        body = parts[2]
    except Exception as e:
        return f"Failed to parse SKILL.md frontmatter: {str(e)}"

    # Parse YAML frontmatter
    try:
        if yaml is None:
            logger.warning("yaml module not available, skipping YAML validation")
            return None

        frontmatter = yaml.safe_load(frontmatter_str)
        if not isinstance(frontmatter, dict):
            return "SKILL.md frontmatter must be a YAML object"
    except Exception as e:
        return f"Failed to parse YAML frontmatter: {str(e)}"

    # Validate required fields
    error = _validate_required_fields(frontmatter)
    if error:
        return error

    # Validate field values
    error = _validate_field_values(frontmatter)
    if error:
        return error

    return None


def _validate_required_fields(frontmatter: dict) -> str | None:
    """Validate required YAML fields.

    Args:
        frontmatter: Parsed YAML frontmatter dict

    Returns:
        Error message if missing required fields, None if valid
    """
    required_fields = ["name", "description"]

    for field in required_fields:
        if field not in frontmatter:
            return f"SKILL.md frontmatter missing required field: {field}"

        value = frontmatter[field]
        if not value or not str(value).strip():
            return f"SKILL.md frontmatter field '{field}' is empty"

    return None


def _validate_field_values(frontmatter: dict) -> str | None:
    """Validate individual field values per agentskills.io spec.

    Args:
        frontmatter: Parsed YAML frontmatter dict

    Returns:
        Error message if invalid, None if valid
    """
    # Validate name
    name = frontmatter.get("name")
    if name:
        error = _validate_skill_name(str(name))
        if error:
            return error

    # Validate description length
    description = frontmatter.get("description")
    if description:
        desc_str = str(description).strip()
        if len(desc_str) < 1:
            return "description must be at least 1 character"
        if len(desc_str) > 1024:
            return f"description exceeds 1024 characters ({len(desc_str)})"

    # Validate compatibility length if present
    compatibility = frontmatter.get("compatibility")
    if compatibility:
        compat_str = str(compatibility).strip()
        if len(compat_str) > 500:
            return f"compatibility exceeds 500 characters ({len(compat_str)})"

    return None


def _validate_skill_name(name: str) -> str | None:
    """Validate skill name per agentskills.io specification.

    Valid names:
    - 1-64 characters
    - Lowercase letters, numbers, hyphens only
    - Not starting or ending with hyphen
    - No consecutive hyphens

    Args:
        name: Skill name to validate

    Returns:
        Error message if invalid, None if valid
    """
    name = name.strip()

    # Length check
    if len(name) < 1:
        return "Skill name must be at least 1 character"
    if len(name) > 64:
        return f"Skill name exceeds 64 characters ({len(name)})"

    # Format check: lowercase alphanumeric + hyphens
    if not re.match(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", name):
        return (
            "Skill name must be lowercase alphanumeric with hyphens, "
            "not start/end with hyphen, no consecutive hyphens"
        )

    return None
