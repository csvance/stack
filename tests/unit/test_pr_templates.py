"""PR template rendering."""

from __future__ import annotations

import pytest

from stack_core import pr_templates


def test_root_template_renders():
    out = pr_templates.render(
        "pr_root",
        ORDER="1",
        COMMIT_SUBJECT="part one",
        COMMIT_BODY="body text",
        STACK_LIST="- Part 1: foo",
        MANIFEST_PATH="redis://stack:proj:manifest:feat",
    )
    assert "[Part 1] part one" in out
    assert "body text" in out
    assert "- Part 1: foo" in out
    assert "redis://stack:proj:manifest:feat" in out


def test_leaf_template_renders():
    out = pr_templates.render(
        "pr_leaf",
        ORDER="2",
        COMMIT_SUBJECT="part two",
        COMMIT_BODY="body",
        ROOT_LINK="https://example/pr/1",
        MANIFEST_PATH="redis://stack:proj:manifest:feat",
    )
    assert "[Part 2]" in out
    assert "Root PR: https://example/pr/1" in out


def test_missing_placeholder_raises():
    with pytest.raises(pr_templates.TemplateRenderError) as exc:
        pr_templates.render(
            "pr_root",
            ORDER="1",
            COMMIT_SUBJECT="s",
            COMMIT_BODY="b",
            STACK_LIST="x",
            # MANIFEST_PATH missing
        )
    assert "MANIFEST_PATH" in str(exc.value)


def test_extra_keys_ignored():
    out = pr_templates.render(
        "pr_leaf",
        ORDER="1",
        COMMIT_SUBJECT="s",
        COMMIT_BODY="b",
        ROOT_LINK="x",
        MANIFEST_PATH="y",
        UNUSED="ignored",
    )
    assert "[Part 1]" in out


def test_build_stack_list(sample_manifest):
    out = pr_templates.build_stack_list(sample_manifest)
    # Top-of-stack first
    lines = out.splitlines()
    assert lines[0].startswith("- Part 3:")
    assert lines[-1].startswith("- Part 1:")
    # No PR ids in sample, so each line says no PR yet
    assert all("_(no PR yet)_" in line for line in lines)


def test_manifest_path_helper():
    assert pr_templates.manifest_path("stack", "myproj", "feat") == "redis://stack:myproj:manifest:feat"
