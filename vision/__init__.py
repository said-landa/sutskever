"""Core math owned by Vision."""

from vision.evaluation import (
    ClassMetrics,
    DetectionEvaluation,
    FrameDetections,
    evaluate_detections,
)
from vision.geometry import Box, Detection, Target, intersection_over_union, suppress_overlaps

__all__ = [
    "Box",
    "ClassMetrics",
    "Detection",
    "DetectionEvaluation",
    "FrameDetections",
    "Target",
    "evaluate_detections",
    "intersection_over_union",
    "suppress_overlaps",
]
