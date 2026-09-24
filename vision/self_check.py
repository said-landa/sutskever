"""Executable checks for the detector math."""

from __future__ import annotations

from math import isclose

from vision.evaluation import FrameDetections, evaluate_detections
from vision.geometry import Box, Detection, Target, intersection_over_union, suppress_overlaps


def run() -> None:
    first = Box(0.0, 0.0, 2.0, 2.0)
    second = Box(1.0, 1.0, 3.0, 3.0)
    assert isclose(intersection_over_union(first, first), 1.0)
    assert isclose(intersection_over_union(first, second), 1.0 / 7.0)
    assert intersection_over_union(first, Box(3.0, 3.0, 4.0, 4.0)) == 0.0

    overlapping = (
        Detection(Box(0.0, 0.0, 2.0, 2.0), class_id=0, score=0.9),
        Detection(Box(0.1, 0.1, 2.1, 2.1), class_id=0, score=0.8),
        Detection(Box(0.1, 0.1, 2.1, 2.1), class_id=1, score=0.7),
    )
    kept = suppress_overlaps(overlapping, iou_threshold=0.5)
    assert kept == (overlapping[0], overlapping[2])
    class_agnostic = suppress_overlaps(
        overlapping, iou_threshold=0.5, class_agnostic=True
    )
    assert class_agnostic == (overlapping[0],)

    frames = (
        FrameDetections(
            frame_id="frame-1",
            predictions=(
                Detection(Box(0.0, 0.0, 2.0, 2.0), class_id=0, score=0.9),
                Detection(Box(0.0, 0.0, 2.0, 2.0), class_id=0, score=0.8),
            ),
            targets=(Target(Box(0.0, 0.0, 2.0, 2.0), class_id=0),),
        ),
        FrameDetections(
            frame_id="frame-2",
            predictions=(
                Detection(Box(4.0, 4.0, 6.0, 6.0), class_id=0, score=0.7),
            ),
            targets=(Target(Box(4.0, 4.0, 6.0, 6.0), class_id=0),),
        ),
    )
    result = evaluate_detections(frames)
    metrics = result.metrics[0.5][0]
    assert metrics.true_positives == (1, 0, 1)
    assert metrics.false_positives == (0, 1, 0)
    assert isclose(metrics.final_precision, 2.0 / 3.0)
    assert metrics.final_recall == 1.0
    assert metrics.average_precision is not None
    assert isclose(metrics.average_precision, 5.0 / 6.0)
    assert isclose(result.mean_average_precision_50 or 0.0, 5.0 / 6.0)

    missed = evaluate_detections(
        (
            FrameDetections(
                frame_id="missed",
                predictions=(),
                targets=(Target(Box(0.0, 0.0, 1.0, 1.0), class_id=2),),
            ),
        )
    ).metrics[0.5][2]
    assert missed.average_precision == 0.0
    assert missed.final_recall == 0.0

    no_target = evaluate_detections(
        (
            FrameDetections(
                frame_id="no-target",
                predictions=(
                    Detection(Box(0.0, 0.0, 1.0, 1.0), class_id=3, score=0.9),
                ),
                targets=(),
            ),
        )
    ).metrics[0.5][3]
    assert no_target.average_precision is None
    assert no_target.true_positives == (0,)
    assert no_target.false_positives == (1,)


if __name__ == "__main__":
    run()
