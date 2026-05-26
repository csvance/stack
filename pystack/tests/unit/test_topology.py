"""Pure functions over manifests."""

from __future__ import annotations

import pytest

from stack_core import topology
from stack_core.exceptions import TopologyError
from tests.conftest import sha


class TestNeighbors:
    def test_bottom_and_top(self, sample_manifest):
        assert topology.bottom_branch(sample_manifest).order == 1
        assert topology.top_branch(sample_manifest).order == 3

    def test_branch_by_name_hit(self, sample_manifest):
        b = topology.branch_by_name(sample_manifest, "feat-stacked-2")
        assert b is not None and b.order == 2

    def test_branch_by_name_miss(self, sample_manifest):
        assert topology.branch_by_name(sample_manifest, "nope") is None

    def test_require_branch_raises(self, sample_manifest):
        with pytest.raises(TopologyError):
            topology.require_branch(sample_manifest, "nope")

    def test_above_below(self, sample_manifest):
        assert topology.branch_above(sample_manifest, "feat-stacked-1").order == 2
        assert topology.branch_above(sample_manifest, "feat-stacked-3") is None
        assert topology.branch_below(sample_manifest, "feat-stacked-1") is None
        assert topology.branch_below(sample_manifest, "feat-stacked-3").order == 2


class TestExpectedParent:
    def test_bottom_uses_base_ref_tip(self, sample_manifest):
        assert (
            topology.expected_parent_sha(sample_manifest, "feat-stacked-1", "deadbeef")
            == "deadbeef"
        )

    def test_middle_uses_previous_commit(self, sample_manifest):
        assert (
            topology.expected_parent_sha(sample_manifest, "feat-stacked-2", "deadbeef")
            == sha(1)
        )


class TestValidateChain:
    def test_passes_for_valid_chain(self, sample_manifest):
        topology.validate_chain(sample_manifest)  # should not raise

    def test_does_not_inspect_bottom_parent(self, sample_manifest):
        """validate_chain only checks adjacent branches; the bottom's parent_sha
        is the base-ref tip captured externally and isn't a chain invariant."""
        topology.validate_chain(sample_manifest)
