"""Box geometry and overlap suppression."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable


@dataclass(frozen=True, slots=True)
class Box:
    """Axis-aligned box in ``left, top, right, bottom`` form."""

    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        values = (self.left, self.top, self.right, self.bottom)
        if not all(isfinite(value) for value in values):
            raise ValueError("box coordinates must be finite")
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("a box must have positive width and height")

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def area(self) -> float:
        return self.width * self.height


@dataclass(frozen=True, slots=True)
class Detection:
    box: Box
    class_id: int
    score: float

    def __post_init__(self) -> None:
        if self.class_id < 0:
            raise ValueError("class_id must be zero or greater")
        if not isfinite(self.score) or not 0.0 <= self.score <= 1.0:
            raise ValueError("score must be between zero and one")


@dataclass(frozen=True, slots=True)
class Target:
    box: Box
    class_id: int

    def __post_init__(self) -> None:
        if self.class_id < 0:
            raise ValueError("class_id must be zero or greater")


def intersection_over_union(first: Box, second: Box) -> float:
    """Return intersection area divided by union area."""

    intersection_width = max(0.0, min(first.right, second.right) - max(first.left, second.left))
    intersection_height = max(0.0, min(first.bottom, second.bottom) - max(first.top, second.top))
    intersection = intersection_width * intersection_height
    union = first.area + second.area - intersection
    return intersection / union


def suppress_overlaps(
    detections: Iterable[Detection],
    iou_threshold: float = 0.5,
    *,
    class_agnostic: bool = False,
) -> tuple[Detection, ...]:
    """Keep high-score detections and remove lower-score overlaps."""

    if not 0.0 <= iou_threshold <= 1.0:
        raise ValueError("iou_threshold must be between zero and one")

    ranked = sorted(enumerate(detections), key=lambda item: (-item[1].score, item[0]))
    kept: list[Detection] = []

    for _, candidate in ranked:
        overlaps_kept = any(
            (class_agnostic or candidate.class_id == accepted.class_id)
            and intersection_over_union(candidate.box, accepted.box) > iou_threshold
            for accepted in kept
        )
        if not overlaps_kept:
            kept.append(candidate)

    return tuple(kept)
