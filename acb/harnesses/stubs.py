"""Stub adapters for harnesses not yet wired up for container-mode generation.

All four harnesses are now fully implemented:
* goose       -- acb/harnesses/goose.py
* claude-code -- acb/harnesses/claude_code.py
* opencode    -- acb/harnesses/opencode.py
* pi          -- acb/harnesses/pi.py

This file is kept as the module that registers OpenCode (the one remaining
harness that ships a binary but whose run_container() is implemented in
opencode.py, not here). If all stubs are eventually removed, this file
and its __init__.py import can be cleaned up.
"""

from __future__ import annotations
