"""Harness stream descriptions and delegation to the Harbor transport."""
from pathlib import Path
import re
from typing import Callable
from acb.harnesses.base import HarnessResult
from acb.transport import EnvironmentCommand

ANSI_ESCAPE_PATTERN = re.compile(
    rb'(?:'
    rb'\x1b\[[0-9;?]*[A-HJKSTfhilmnsu]|'  # CSI: ESC[ params letter
    rb'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|'  # OSC: ESC] ... (BEL|ESC\)
    rb'\x1b[=>]|'  # Simple: ESC= or ESC>
    rb'\x1b\([0-9A-B]|'  # Charset: ESC( ...
    rb'\x1b\)0'  # Charset: ESC)0
    rb')'
)

DescribeEvent = Callable[[dict], tuple[str | None, bool]]


def execute(cmd: EnvironmentCommand, transcript_path: Path,
            timeout: int, describe_event: DescribeEvent, tracker=None,
            tracker_key: str | None = None) -> HarnessResult:
    if not isinstance(cmd, EnvironmentCommand):
        raise TypeError("harness execution requires an EnvironmentCommand")
    return cmd.transport.execute(cmd, transcript_path=transcript_path,
                                 timeout=timeout, describe_event=describe_event,
                                 tracker=tracker, tracker_key=tracker_key)
