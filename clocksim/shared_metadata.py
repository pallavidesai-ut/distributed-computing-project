"""Metadata accounting helpers for sibling-set encodings.

The core simulator stores each ``VersionRecord`` with its own stamp.  That is a
conservative per-version accounting model, but a practical DVV object store can
factor common sibling ancestry once and then keep one dot per sibling version.
These helpers model that normalized representation without changing clock
comparison semantics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .clocks import BaseStamp, DVVStamp
from .context import CausalContext, Dot, compact_context, union_contexts


def metadata_json_bytes(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def dot_payload(dot: Dot) -> list[Any]:
    return dot.to_list()


def context_payload(context: CausalContext) -> dict[str, Any]:
    return {
        "summary": dict(sorted(context.prefix.items())),
        "dots": [dot_payload(dot) for dot in sorted(context.dots)],
    }


def context_component_count(context: CausalContext) -> int:
    return len(context.prefix) + len(context.dots)


def _compact_events(events: Iterable[Dot]) -> CausalContext:
    return compact_context({}, set(events))


def intersect_contexts(contexts: Sequence[CausalContext]) -> CausalContext:
    if not contexts:
        return CausalContext()
    common_events = contexts[0].materialize()
    for context in contexts[1:]:
        common_events.intersection_update(context.materialize())
    return _compact_events(common_events)


@dataclass(frozen=True)
class RepeatedStampSetEncoding:
    """Sibling-set encoding that repeats each version stamp independently."""

    stamps: tuple[BaseStamp, ...]

    def payload(self) -> dict[str, Any]:
        versions = []
        for stamp in self.stamps:
            serialized = stamp.serialize()
            serialized.pop("type", None)
            versions.append(serialized)
        return {"versions": versions}

    def metadata_bytes(self) -> int:
        return metadata_json_bytes(self.payload())

    def metadata_component_count(self) -> int:
        return sum(stamp.metadata_component_count() for stamp in self.stamps)


@dataclass(frozen=True)
class SharedDVVSetEncoding:
    """Normalized DVV sibling set: common context once, one dot per version.

    ``extras`` preserves exactness for less regular sibling sets where a version
    carries causal events that are not common to every sibling and are not that
    version's own dot.  In the intended wide-contention experiment, extras are
    empty and the representation is exactly "shared summary + sibling dots".
    """

    shared: CausalContext
    dots: tuple[Dot, ...]
    extras: tuple[CausalContext, ...]

    def payload(self) -> dict[str, Any]:
        versions: list[dict[str, Any]] = []
        for dot, extra in zip(self.dots, self.extras, strict=True):
            version: dict[str, Any] = {"dot": dot_payload(dot)}
            if extra.prefix or extra.dots:
                version["extra"] = context_payload(extra)
            versions.append(version)
        return {
            "shared": context_payload(self.shared),
            "versions": versions,
        }

    def metadata_bytes(self) -> int:
        return metadata_json_bytes(self.payload())

    def metadata_component_count(self) -> int:
        return (
            context_component_count(self.shared)
            + len(self.dots)
            + sum(context_component_count(extra) for extra in self.extras)
        )

    def reconstructed_contexts(self) -> list[CausalContext]:
        contexts: list[CausalContext] = []
        for dot, extra in zip(self.dots, self.extras, strict=True):
            contexts.append(union_contexts([self.shared, extra, CausalContext(dots={dot})]))
        return contexts


def repeated_stamp_set_encoding(stamps: Sequence[BaseStamp]) -> RepeatedStampSetEncoding:
    return RepeatedStampSetEncoding(tuple(stamps))


def sibling_set_encoding(
    stamps: Sequence[BaseStamp],
) -> RepeatedStampSetEncoding | SharedDVVSetEncoding:
    stamp_tuple = tuple(stamps)
    if stamp_tuple and all(isinstance(stamp, DVVStamp) for stamp in stamp_tuple):
        return shared_dvv_set_encoding(stamp_tuple)
    return repeated_stamp_set_encoding(stamp_tuple)


def shared_dvv_set_encoding(stamps: Sequence[BaseStamp]) -> SharedDVVSetEncoding:
    """Return exact shared-summary DVV accounting for a sibling set."""

    stamp_tuple = tuple(stamps)
    if not stamp_tuple:
        return SharedDVVSetEncoding(CausalContext(), tuple(), tuple())

    contexts = [stamp.represented_context() for stamp in stamp_tuple]
    version_dots = {stamp.dot for stamp in stamp_tuple}
    shared_events = intersect_contexts(contexts).materialize() - version_dots
    shared = _compact_events(shared_events)
    shared_materialized = shared.materialize()

    extras: list[CausalContext] = []
    for stamp, context in zip(stamp_tuple, contexts, strict=True):
        extra_events = context.materialize() - shared_materialized - {stamp.dot}
        extras.append(_compact_events(extra_events))

    return SharedDVVSetEncoding(
        shared=shared,
        dots=tuple(stamp.dot for stamp in stamp_tuple),
        extras=tuple(extras),
    )
