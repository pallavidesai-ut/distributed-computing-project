from __future__ import annotations

from clocksim import (
    CausalContext,
    DottedVersionVectorModel,
    VersionVectorModel,
    repeated_stamp_set_encoding,
    shared_dvv_set_encoding,
)
from clocksim.clocks import BaseStamp, ClockModel


def issue_concurrent_stamps(
    model: ClockModel,
    base_context: CausalContext,
    sibling_count: int,
) -> list[BaseStamp]:
    state = model.make_state("n1")
    return [
        model.issue_stamp(
            state,
            "k0",
            base_context.clone(),
            now=0.0,
            actor_id=f"writer-{index}",
        )
        for index in range(sibling_count)
    ]


def test_current_dvv_repeats_summary_on_each_sibling_stamp() -> None:
    base_context = CausalContext(prefix={"a": 1, "b": 1, "c": 1})
    stamps = issue_concurrent_stamps(DottedVersionVectorModel(), base_context, 4)

    assert all(stamp.serialize()["summary"] == base_context.prefix for stamp in stamps)
    assert sum(stamp.metadata_component_count() for stamp in stamps) == 16


def test_shared_dvv_set_reconstructs_original_version_contexts() -> None:
    base_context = CausalContext(prefix={f"a{index}": 1 for index in range(8)})
    stamps = issue_concurrent_stamps(DottedVersionVectorModel(), base_context, 6)

    encoding = shared_dvv_set_encoding(stamps)

    assert encoding.shared.prefix == base_context.prefix
    assert all(not extra.prefix and not extra.dots for extra in encoding.extras)
    assert encoding.reconstructed_contexts() == [
        stamp.represented_context() for stamp in stamps
    ]


def test_shared_dvv_beats_repeated_vv_for_wide_sibling_set() -> None:
    base_context = CausalContext(prefix={f"a{index:04d}": 1 for index in range(32)})
    vv_stamps = issue_concurrent_stamps(VersionVectorModel(), base_context, 16)
    dvv_stamps = issue_concurrent_stamps(DottedVersionVectorModel(), base_context, 16)

    repeated_vv = repeated_stamp_set_encoding(vv_stamps)
    current_dvv = repeated_stamp_set_encoding(dvv_stamps)
    shared_dvv = shared_dvv_set_encoding(dvv_stamps)

    assert current_dvv.metadata_bytes() > repeated_vv.metadata_bytes()
    assert shared_dvv.metadata_bytes() < repeated_vv.metadata_bytes()
    assert shared_dvv.metadata_component_count() == 32 + 16
