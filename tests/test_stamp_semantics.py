from __future__ import annotations

from clocksim import DVVStamp, Dot, VVStamp


def test_vv_stamp_represents_full_prefix_history() -> None:
    stamp = VVStamp(vector={"a": 2, "b": 1}, new_dot=Dot("a", 2))

    represented = stamp.represented_context()

    assert represented.contains(Dot("a", 1))
    assert represented.contains(Dot("a", 2))
    assert represented.contains(Dot("b", 1))
    assert not represented.contains(Dot("b", 2))


def test_dvv_stamp_represents_summary_exceptions_and_new_dot() -> None:
    stamp = DVVStamp(
        summary={"a": 1},
        exceptions={Dot("b", 3)},
        new_dot=Dot("c", 1),
    )

    represented = stamp.represented_context()

    assert represented.contains(Dot("a", 1))
    assert represented.contains(Dot("b", 3))
    assert represented.contains(Dot("c", 1))
    assert not represented.contains(Dot("b", 2))


def test_metadata_bytes_excludes_type_label() -> None:
    vv = VVStamp(vector={"a": 1}, new_dot=Dot("a", 1))
    dvv = DVVStamp(summary={}, exceptions=set(), new_dot=Dot("a", 1))

    assert vv.metadata_bytes() > 0
    assert dvv.metadata_bytes() > 0
    assert "type" in vv.serialize()
    assert "type" in dvv.serialize()
