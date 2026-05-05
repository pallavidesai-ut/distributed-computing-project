"""Interval Tree Clock data structures.

This module implements the core Interval Tree Clock (ITC) algebra from
Almeida, Baquero, and Fonte's dynamic logical clocks: identity trees,
event trees, fork, event, join, and partial-order comparison.  The shape is
inspired by the small public-domain ``py-itc`` reference implementation, but
is kept local and typed so the simulator has no extra runtime dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ITCIdTree:
    """ITC identity tree.

    A leaf is either ``0`` (no ownership) or ``1`` (full ownership of the
    current interval).  An internal node splits ownership between the left and
    right sub-intervals.
    """

    value: int | None = None
    left: "ITCIdTree | None" = None
    right: "ITCIdTree | None" = None

    @classmethod
    def leaf(cls, value: int) -> "ITCIdTree":
        if value not in {0, 1}:
            raise ValueError("ITC id leaves must be 0 or 1")
        return cls(value=value)

    @classmethod
    def zero(cls) -> "ITCIdTree":
        return cls.leaf(0)

    @classmethod
    def one(cls) -> "ITCIdTree":
        return cls.leaf(1)

    @classmethod
    def node(cls, left: "ITCIdTree", right: "ITCIdTree") -> "ITCIdTree":
        return cls(value=None, left=left, right=right).normalize()

    @property
    def is_leaf(self) -> bool:
        return self.value is not None

    def clone(self) -> "ITCIdTree":
        return ITCIdTree(
            value=self.value,
            left=self.left.clone() if self.left is not None else None,
            right=self.right.clone() if self.right is not None else None,
        )

    def normalize(self) -> "ITCIdTree":
        if self.is_leaf:
            return self
        if self.left is None or self.right is None:
            raise ValueError("internal ITC id node must have two children")
        self.left.normalize()
        self.right.normalize()
        if self.left.is_leaf and self.right.is_leaf and self.left.value == self.right.value:
            self.value = self.left.value
            self.left = None
            self.right = None
        return self

    def split(self) -> tuple["ITCIdTree", "ITCIdTree"]:
        """Split this identity into two disjoint identities."""
        if self.is_leaf:
            if self.value == 0:
                return ITCIdTree.zero(), ITCIdTree.zero()
            return (
                ITCIdTree.node(ITCIdTree.one(), ITCIdTree.zero()),
                ITCIdTree.node(ITCIdTree.zero(), ITCIdTree.one()),
            )

        assert self.left is not None and self.right is not None
        left_is_zero = self.left.is_leaf and self.left.value == 0
        right_is_zero = self.right.is_leaf and self.right.value == 0
        if left_is_zero and not right_is_zero:
            first, second = self.right.split()
            return (
                ITCIdTree.node(ITCIdTree.zero(), first),
                ITCIdTree.node(ITCIdTree.zero(), second),
            )
        if right_is_zero and not left_is_zero:
            first, second = self.left.split()
            return (
                ITCIdTree.node(first, ITCIdTree.zero()),
                ITCIdTree.node(second, ITCIdTree.zero()),
            )
        return (
            ITCIdTree.node(self.left.clone(), ITCIdTree.zero()),
            ITCIdTree.node(ITCIdTree.zero(), self.right.clone()),
        )

    def union(self, other: "ITCIdTree") -> "ITCIdTree":
        """Union two identity trees.

        ITC joins are normally applied to disjoint identities, but this method
        is total and idempotent so tests and diagnostics can safely join the
        same identity more than once.
        """
        if self.is_leaf and other.is_leaf:
            return ITCIdTree.leaf(max(int(self.value), int(other.value)))
        if self.is_leaf:
            left = ITCIdTree.leaf(int(self.value))
            right = ITCIdTree.leaf(int(self.value))
        else:
            assert self.left is not None and self.right is not None
            left = self.left
            right = self.right
        if other.is_leaf:
            other_left = ITCIdTree.leaf(int(other.value))
            other_right = ITCIdTree.leaf(int(other.value))
        else:
            assert other.left is not None and other.right is not None
            other_left = other.left
            other_right = other.right
        return ITCIdTree.node(left.union(other_left), right.union(other_right))

    def node_count(self) -> int:
        if self.is_leaf:
            return 1
        assert self.left is not None and self.right is not None
        return 1 + self.left.node_count() + self.right.node_count()

    def to_obj(self) -> Any:
        if self.is_leaf:
            return int(self.value)
        assert self.left is not None and self.right is not None
        return [self.left.to_obj(), self.right.to_obj()]

    def __str__(self) -> str:
        return str(self.to_obj())


@dataclass
class ITCEventTree:
    """ITC event tree.

    A leaf stores a scalar event height.  An internal node stores a shared base
    value plus residual left/right event trees.
    """

    value: int = 0
    left: "ITCEventTree | None" = None
    right: "ITCEventTree | None" = None

    @classmethod
    def leaf(cls, value: int = 0) -> "ITCEventTree":
        return cls(value=value)

    @classmethod
    def node(
        cls,
        value: int,
        left: "ITCEventTree",
        right: "ITCEventTree",
    ) -> "ITCEventTree":
        return cls(value=value, left=left, right=right).normalize()

    @property
    def is_leaf(self) -> bool:
        return self.left is None and self.right is None

    def clone(self) -> "ITCEventTree":
        return ITCEventTree(
            value=self.value,
            left=self.left.clone() if self.left is not None else None,
            right=self.right.clone() if self.right is not None else None,
        )

    def expand(self) -> None:
        if self.is_leaf:
            self.left = ITCEventTree.leaf(0)
            self.right = ITCEventTree.leaf(0)

    def lift(self, amount: int) -> "ITCEventTree":
        clone = self.clone()
        clone.value += amount
        return clone

    def sink_in_place(self, amount: int) -> None:
        self.value -= amount

    def normalize(self) -> "ITCEventTree":
        if self.is_leaf:
            return self
        if self.left is None or self.right is None:
            raise ValueError("internal ITC event node must have two children")
        self.left.normalize()
        self.right.normalize()
        if self.left.is_leaf and self.right.is_leaf and self.left.value == self.right.value:
            self.value += self.left.value
            self.left = None
            self.right = None
            return self
        shared = min(self.left.value, self.right.value)
        if shared:
            self.value += shared
            self.left.sink_in_place(shared)
            self.right.sink_in_place(shared)
        return self

    def join(self, other: "ITCEventTree") -> "ITCEventTree":
        """Pointwise maximum of two event trees."""
        if self.is_leaf and other.is_leaf:
            return ITCEventTree.leaf(max(self.value, other.value))

        left = self.clone()
        right = other.clone()
        if left.is_leaf:
            left.expand()
        if right.is_leaf:
            right.expand()
        assert left.left is not None and left.right is not None
        assert right.left is not None and right.right is not None

        if left.value > right.value:
            return right.join(left)

        delta = right.value - left.value
        return ITCEventTree.node(
            left.value,
            left.left.join(right.left.lift(delta)),
            left.right.join(right.right.lift(delta)),
        )

    def leq(self, other: "ITCEventTree") -> bool:
        """Return true when every interval counter in ``self`` is <= ``other``."""
        if self.is_leaf and other.is_leaf:
            return self.value <= other.value
        if self.is_leaf and not other.is_leaf:
            if self.value < other.value:
                return True
            expanded = self.clone()
            expanded.expand()
            return expanded.leq(other)
        if not self.is_leaf and other.is_leaf:
            if self.value > other.value:
                return False
            assert self.left is not None and self.right is not None
            return self.left.lift(self.value).leq(other) and self.right.lift(self.value).leq(other)

        assert self.left is not None and self.right is not None
        assert other.left is not None and other.right is not None
        if self.value > other.value:
            return False
        return self.left.lift(self.value).leq(other.left.lift(other.value)) and self.right.lift(
            self.value
        ).leq(other.right.lift(other.value))

    def height(self) -> None:
        """Collapse this tree to a leaf storing its maximum interval height."""
        if not self.is_leaf:
            assert self.left is not None and self.right is not None
            self.left.height()
            self.right.height()
            self.value += max(self.left.value, self.right.value)
            self.left = None
            self.right = None

    def node_count(self) -> int:
        if self.is_leaf:
            return 1
        assert self.left is not None and self.right is not None
        return 1 + self.left.node_count() + self.right.node_count()

    def to_obj(self) -> Any:
        if self.is_leaf:
            return self.value
        assert self.left is not None and self.right is not None
        return [self.value, self.left.to_obj(), self.right.to_obj()]

    def __str__(self) -> str:
        return str(self.to_obj())


@dataclass
class ITCCoreStamp:
    """A pure ITC stamp: identity plus event tree."""

    identity: ITCIdTree
    event: ITCEventTree

    @classmethod
    def seed(cls) -> "ITCCoreStamp":
        return cls(ITCIdTree.one(), ITCEventTree.leaf(0))

    def clone(self) -> "ITCCoreStamp":
        return ITCCoreStamp(self.identity.clone(), self.event.clone())

    def fork(self) -> tuple["ITCCoreStamp", "ITCCoreStamp"]:
        left_id, right_id = self.identity.split()
        return (
            ITCCoreStamp(left_id, self.event.clone()),
            ITCCoreStamp(right_id, self.event.clone()),
        )

    def join(self, other: "ITCCoreStamp") -> "ITCCoreStamp":
        return ITCCoreStamp(self.identity.union(other.identity), self.event.join(other.event))

    def peek(self) -> "ITCCoreStamp":
        return ITCCoreStamp(ITCIdTree.zero(), self.event.clone())

    def event_occurred(self) -> None:
        before = self.event.clone()
        self.fill()
        if before == self.event:
            self.grow()
        self.event.normalize()

    def fill(self) -> "ITCCoreStamp":
        if self.identity.is_leaf:
            if self.identity.value == 1:
                self.event.height()
            return self
        if self.event.is_leaf:
            return self

        assert self.identity.left is not None and self.identity.right is not None
        assert self.event.left is not None and self.event.right is not None
        left_full = self.identity.left.is_leaf and self.identity.left.value == 1
        right_full = self.identity.right.is_leaf and self.identity.right.value == 1
        if left_full:
            ITCCoreStamp(self.identity.right, self.event.right).fill()
            self.event.left.height()
            self.event.left.value = max(self.event.left.value, self.event.right.value)
            self.event.normalize()
        elif right_full:
            ITCCoreStamp(self.identity.left, self.event.left).fill()
            self.event.right.height()
            self.event.right.value = max(self.event.right.value, self.event.left.value)
            self.event.normalize()
        else:
            ITCCoreStamp(self.identity.left, self.event.left).fill()
            ITCCoreStamp(self.identity.right, self.event.right).fill()
            self.event.normalize()
        return self

    def grow(self) -> int:
        if self.identity.is_leaf and self.identity.value == 1 and self.event.is_leaf:
            self.event.value += 1
            return 0
        if self.event.is_leaf:
            self.event.expand()
            return self.grow() + 1_000_000
        if self.identity.is_leaf:
            return -1

        assert self.identity.left is not None and self.identity.right is not None
        assert self.event.left is not None and self.event.right is not None
        left_zero = self.identity.left.is_leaf and self.identity.left.value == 0
        right_zero = self.identity.right.is_leaf and self.identity.right.value == 0
        if left_zero:
            return ITCCoreStamp(self.identity.right, self.event.right).grow() + 1
        if right_zero:
            return ITCCoreStamp(self.identity.left, self.event.left).grow() + 1

        old_left = self.event.left.clone()
        old_right = self.event.right.clone()
        left_cost = ITCCoreStamp(self.identity.left, self.event.left).grow()
        self.event.left = old_left
        right_cost = ITCCoreStamp(self.identity.right, self.event.right).grow()
        if left_cost < right_cost:
            self.event.right = old_right
            ITCCoreStamp(self.identity.left, self.event.left).grow()
            return left_cost + 1
        return right_cost + 1

    def leq(self, other: "ITCCoreStamp") -> bool:
        return self.event.leq(other.event)

    def to_obj(self) -> dict[str, Any]:
        return {"id": self.identity.to_obj(), "event": self.event.to_obj()}
