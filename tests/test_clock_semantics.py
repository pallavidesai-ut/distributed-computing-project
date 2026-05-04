from __future__ import annotations

import unittest

from clocksim import (
    CausalContext,
    Dot,
    DottedVersionVectorModel,
    LeaseDottedVersionVectorModel,
    VnodeVersionVectorModel,
)


class ClockSemanticTests(unittest.TestCase):
    def test_dvv_preserves_same_coordinator_concurrency(self) -> None:
        model = DottedVersionVectorModel()
        state = model.make_state("n1")
        empty_read = CausalContext()

        first = model.issue_stamp(state, "k0", empty_read, now=0.0, actor_id="c1")
        second = model.issue_stamp(state, "k0", empty_read, now=0.0, actor_id="c2")

        self.assertEqual(model.compare_stamps(second, first), "concurrent")

    def test_vnode_vv_collapses_same_coordinator_concurrency(self) -> None:
        model = VnodeVersionVectorModel()
        state = model.make_state("n1")
        empty_read = CausalContext()

        first = model.issue_stamp(state, "k0", empty_read, now=0.0, actor_id="c1")
        second = model.issue_stamp(state, "k0", empty_read, now=0.0, actor_id="c2")

        self.assertEqual(model.compare_stamps(second, first), "dominates")

    def test_lease_dvv_prunes_expired_actor_history(self) -> None:
        model = LeaseDottedVersionVectorModel(lease_duration=1.0)
        state = model.make_state("n1")
        read_context = CausalContext(prefix={"n2": 3}, dots={Dot("n3", 1)})

        stamp = model.issue_stamp(state, "k0", read_context, now=10.0, actor_id="c1")
        represented = stamp.represented_context()

        self.assertTrue(stamp.was_pruned())
        self.assertEqual(stamp.pruned_actor_count(), 2)
        self.assertEqual(stamp.pruned_event_count(), 4)
        self.assertNotIn("n2", represented.prefix)
        self.assertNotIn(Dot("n3", 1), represented.dots)


if __name__ == "__main__":
    unittest.main()
