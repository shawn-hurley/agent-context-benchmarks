"""Centralized logging configuration for ACB.

Provides thread-safe, always-safe logger access. Call setup_acb_logger()
once at startup, then use get_logger() or the safe logging functions
from any thread without fear of crashes.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

# Global logger instance (initialized once at startup)
_logger: Optional[logging.Logger] = None
_log_file: Optional[Path] = None


def setup_acb_logger(log_file: Path, verbose: bool = False) -> logging.Logger:
    """Setup the global ACB logger (call once at startup).
    
    Creates a logger named "acb" with:
    - File handler: Always writes to log_file
    - Console handler: Only if verbose=True
    
    Args:
        log_file: Path to write logs to
        verbose: If True, also log to console (stderr)
    
    Returns:
        Configured logger instance
    """
    global _logger, _log_file
    
    _logger = logging.getLogger("acb")
    _logger.setLevel(logging.DEBUG)
    
    # Clear any existing handlers from previous setup attempts
    _logger.handlers.clear()
    
    # File handler: always writes all debug and above
    try:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_format = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_format)
        _logger.addHandler(file_handler)
        _log_file = log_file
    except Exception as e:
        # If file logging fails, continue with at least stderr/console
        _logger.addHandler(logging.NullHandler())
    
    # Console handler: only if verbose mode
    if verbose:
        try:
            console_handler = logging.StreamHandler(sys.stderr)
            console_handler.setLevel(logging.DEBUG)
            console_format = logging.Formatter(
                '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            console_handler.setFormatter(console_format)
            _logger.addHandler(console_handler)
        except Exception:
            pass  # Console logging failed, but file handler works
    
    _logger.info(f"ACB run started, logging to {log_file}")
    return _logger


def get_logger() -> logging.Logger:
    """Get the ACB logger (safe to call from any thread).
    
    If setup_acb_logger() was never called, returns a logger with a NullHandler
    that won't crash but also won't log anything. Always safe to use.
    
    Returns:
        Logger instance (always returns something, never None)
    """
    global _logger
    
    if _logger is None:
        # Fallback: create basic logger if not initialized
        _logger = logging.getLogger("acb")
        if not _logger.handlers:
            # Add NullHandler to prevent "No handlers" warnings
            # and to prevent logs from being propagated to root logger
            _logger.addHandler(logging.NullHandler())
    
    return _logger


def log_debug(msg: str) -> None:
    """Safe debug logging (won't crash if logger fails).
    
    Args:
        msg: Debug message to log
    """
    try:
        get_logger().debug(msg)
    except Exception:
        pass  # Silent fail - don't kill threads or displays


def log_info(msg: str) -> None:
    """Safe info logging (won't crash if logger fails).
    
    Args:
        msg: Info message to log
    """
    try:
        get_logger().info(msg)
    except Exception:
        pass  # Silent fail


def log_warning(msg: str) -> None:
    """Safe warning logging (won't crash if logger fails).
    
    Args:
        msg: Warning message to log
    """
    try:
        get_logger().warning(msg)
    except Exception:
        pass  # Silent fail


def log_error(msg: str) -> None:
    """Safe error logging (won't crash if logger fails).
    
    Args:
        msg: Error message to log
    """
    try:
        get_logger().error(msg)
    except Exception:
        pass  # Silent fail


def get_log_file() -> Optional[Path]:
    """Get the path to the log file (if one was set up).
    
    Returns:
        Path to log file, or None if not yet configured
    """
    return _log_file
