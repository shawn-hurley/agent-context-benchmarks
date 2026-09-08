"""Skills infrastructure for ACB harnesses.

Skills follow the Agent Skills standard (agentskills.io) and are installed
to harness-specific directories. Each skill is a directory containing SKILL.md
with YAML frontmatter (name, description, license, etc.) and supporting files.
"""

from .installer import SkillInstaller, SkillResult

__all__ = ["SkillInstaller", "SkillResult"]
