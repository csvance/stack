"""Contract test on the JSON shape the decomposer skill must produce."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from stack_core.operations.create import Sentinel


def _representative_sentinel() -> dict:
    return {
        "prefix": "feat",
        "branches": ["feat-stacked-1", "feat-stacked-2", "feat-stacked-3"],
        "base_ref": "origin/main",
        "source_branch": "feat",
        "source_branch_tip": "0123456789abcdef0123456789abcdef01234567",
        "branch_suffix": "-stacked-",
        "completion_timestamp": "2026-05-25T14:23:11Z",
    }


def test_representative_sentinel_parses():
    payload = _representative_sentinel()
    s = Sentinel.model_validate_json(json.dumps(payload))
    assert s.prefix == "feat"
    assert s.branches == ["feat-stacked-1", "feat-stacked-2", "feat-stacked-3"]
    assert s.branch_suffix == "-stacked-"


def test_empty_branches_rejected():
    payload = _representative_sentinel()
    payload["branches"] = []
    with pytest.raises(ValidationError):
        Sentinel.model_validate_json(json.dumps(payload))


def test_missing_required_field_rejected():
    payload = _representative_sentinel()
    del payload["source_branch_tip"]
    with pytest.raises(ValidationError):
        Sentinel.model_validate_json(json.dumps(payload))


def test_extra_field_rejected():
    payload = _representative_sentinel()
    payload["unknown_field"] = "x"
    with pytest.raises(ValidationError):
        Sentinel.model_validate_json(json.dumps(payload))


def test_default_branch_suffix():
    payload = _representative_sentinel()
    del payload["branch_suffix"]
    s = Sentinel.model_validate_json(json.dumps(payload))
    assert s.branch_suffix == "-stacked-"
