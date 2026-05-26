"""Render PR description templates with simple ``{{KEY}}`` substitution.

Templates live at ``stack_core/share/*.md`` and are loaded via
``importlib.resources``. The renderer requires every ``{{KEY}}`` to be supplied
(missing context raises) and tolerates extra keys (silently unused).
"""

from __future__ import annotations

import re
from importlib.resources import files
from typing import Literal

from stack_core.types import Manifest

TemplateName = Literal["pr_root", "pr_leaf", "pr_root_landed"]

_PLACEHOLDER_RE = re.compile(r"\{\{(?P<key>[A-Z_][A-Z0-9_]*)\}\}")


class TemplateRenderError(ValueError):
    """Raised when required placeholders are missing from the render context."""


def load(name: TemplateName) -> str:
    return (files("stack_core.share") / f"{name}.md").read_text(encoding="utf-8")


def required_placeholders(template: str) -> set[str]:
    return {match.group("key") for match in _PLACEHOLDER_RE.finditer(template)}


def render(name: TemplateName, **context: str) -> str:
    template = load(name)
    required = required_placeholders(template)
    missing = required - context.keys()
    if missing:
        raise TemplateRenderError(
            f"template {name!r} missing placeholders: {sorted(missing)!r}"
        )

    def _sub(match: re.Match[str]) -> str:
        return context[match.group("key")]

    return _PLACEHOLDER_RE.sub(_sub, template)


def manifest_path(key_prefix: str, project: str, prefix: str) -> str:
    """Render the redis://-style manifest pointer used in PR descriptions."""
    return f"redis://{key_prefix}:{project}:manifest:{prefix}"


def build_stack_list(manifest: Manifest) -> str:
    """Render the markdown list of PRs for the ``{{STACK_LIST}}`` placeholder.

    Top of stack is listed first (matching the order PRs are reviewed and
    landed bottom-up). Branches without a recorded PR id are shown as plain
    text.
    """
    lines: list[str] = []
    for entry in reversed(manifest.branches):
        if entry.pr_id is not None:
            link = entry.pr_url or f"#{entry.pr_id}"
            lines.append(f"- Part {entry.order}: [{entry.subject}]({link})")
        else:
            lines.append(f"- Part {entry.order}: {entry.subject} _(no PR yet)_")
    return "\n".join(lines)
