"""Structured stdout/stderr helpers for CLI commands.

Honors ``NO_COLOR`` and ``--no-color`` to disable ANSI styling. Output goes to
stderr for informational messages and stdout for the command's primary result,
matching the bash CLI's split.
"""

from __future__ import annotations

import os
import sys
from typing import TextIO

_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_RED = "\x1b[31m"
_YELLOW = "\x1b[33m"
_GREEN = "\x1b[32m"
_DIM = "\x1b[2m"


def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stderr.isatty()


def _wrap(color: str, text: str) -> str:
    if not _color_enabled():
        return text
    return f"{color}{text}{_RESET}"


def info(message: str, *, file: TextIO | None = None) -> None:
    print(message, file=file or sys.stderr)


def success(message: str, *, file: TextIO | None = None) -> None:
    print(_wrap(_GREEN, message), file=file or sys.stderr)


def warn(message: str, *, file: TextIO | None = None) -> None:
    print(_wrap(_YELLOW, f"warning: {message}"), file=file or sys.stderr)


def error(message: str, *, file: TextIO | None = None) -> None:
    print(_wrap(_RED, f"error: {message}"), file=file or sys.stderr)


def emphasize(text: str) -> str:
    return _wrap(_BOLD, text)


def dim(text: str) -> str:
    return _wrap(_DIM, text)
