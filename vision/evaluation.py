"""Detector matching, precision, recall and average precision."""

from __future__ import annotations

from dataclasses import dataclass
from math import fsum
from typing import Iterable

from vision.geometry import Detection, Target, intersection_over_union


@dataclass(frozen=True, slots=True)
class FrameDetections:
    frame_id: str
    predictions: tuple[Detection, ...]
    targets: tuple[Target, ...]


@dataclass(frozen=True, slots=True)
class ClassMetrics:
    class_id: int
    iou_threshold: float
    target_count: int
    prediction_count: int
    scores: tuple[float, ...]
    true_positives: tuple[int, ...]
    false_positives: tuple[int, ...]
    precision: tuple[float, ...]
    recall: tuple[float, ...]
    average_precision: float | None

    @property
    def final_precision(self) -> float:
        return self.precision[-1] if self.precision else 0.0

    @property
    def final_recall(self) -> float:
        return self.recall[-1] if self.recall else 0.0


@dataclass(frozen=True, slots=True)
class DetectionEvaluation:
    iou_thresholds: tuple[float, ...]
    classes: tuple[int, ...]
    metrics: dict[float, dict[int, ClassMetrics]]
    mean_average_precision: float | None
    mean_average_precision_50: float | None


def _average_precision(precision: tuple[float, ...], recall: tuple[float, ...]) -> float:
    padded_recall = [0.0, *recall, 1.0]
    padded_precision = [0.0, *precision, 0.0]

    for index in range(len(padded_precision) - 2, -1, -1):
        padded_precision[index] = max(padded_precision[index], padded_precision[index + 1])

    areas = (
        (padded_recall[index] - padded_recall[index - 1]) * padded_precision[index]
        for index in range(1, len(padded_recall))
        if padded_recall[index] != padded_recall[index - 1]
    )
    return fsum(areas)


def _evaluate_class(
    frames: tuple[FrameDetections, ...],
    class_id: int,
    iou_threshold: float,
) -> ClassMetrics:
    targets_by_frame: dict[int, tuple[Target, ...]] = {}
    matched_by_frame: dict[int, set[int]] = {}
    ranked_predictions: list[tuple[float, int, int, Detection]] = []

    for frame_index, frame in enumerate(frames):
        targets = tuple(target for target in frame.targets if target.class_id == class_id)
        targets_by_frame[frame_index] = targets
        matched_by_frame[frame_index] = set()
        for prediction_index, prediction in enumerate(frame.predictions):
            if prediction.class_id == class_id:
                ranked_predictions.append(
                    (prediction.score, frame_index, prediction_index, prediction)
                )

    ranked_predictions.sort(key=lambda item: (-item[0], item[1], item[2]))
    target_count = sum(len(targets) for targets in targets_by_frame.values())
    true_positives: list[int] = []
    false_positives: list[int] = []
    scores: list[float] = []

    for score, frame_index, _, prediction in ranked_predictions:
        targets = targets_by_frame[frame_index]
        matched_targets = matched_by_frame[frame_index]
        candidates = [
            (intersection_over_union(prediction.box, target.box), target_index)
            for target_index, target in enumerate(targets)
            if target_index not in matched_targets
        ]
        best_iou, best_target = max(candidates, default=(0.0, -1))
        is_match = best_target >= 0 and best_iou >= iou_threshold

        scores.append(score)
        true_positives.append(int(is_match))
        false_positives.append(int(not is_match))
        if is_match:
            matched_targets.add(best_target)

    cumulative_true = 0
    cumulative_false = 0
    precision: list[float] = []
    recall: list[float] = []

    for true_positive, false_positive in zip(true_positives, false_positives, strict=True):
        cumulative_true += true_positive
        cumulative_false += false_positive
        precision.append(cumulative_true / (cumulative_true + cumulative_false))
        recall.append(cumulative_true / target_count if target_count else 0.0)

    average_precision = (
        _average_precision(tuple(precision), tuple(recall)) if target_count else None
    )
    return ClassMetrics(
        class_id=class_id,
        iou_threshold=iou_threshold,
        target_count=target_count,
        prediction_count=len(ranked_predictions),
        scores=tuple(scores),
        true_positives=tuple(true_positives),
        false_positives=tuple(false_positives),
        precision=tuple(precision),
        recall=tuple(recall),
        average_precision=average_precision,
    )


def evaluate_detections(
    frames: Iterable[FrameDetections],
    iou_thresholds: Iterable[float] = (0.5,),
) -> DetectionEvaluation:
    """Evaluate predictions with one-to-one, score-ordered target matching."""

    saved_frames = tuple(frames)
    frame_ids = [frame.frame_id for frame in saved_frames]
    if len(frame_ids) != len(set(frame_ids)):
        raise ValueError("frame_id values must be unique")

    thresholds = tuple(iou_thresholds)
    if not thresholds:
        raise ValueError("at least one IoU threshold is required")
    if any(not 0.0 <= threshold <= 1.0 for threshold in thresholds):
        raise ValueError("IoU thresholds must be between zero and one")
    if len(thresholds) != len(set(thresholds)):
        raise ValueError("IoU thresholds must be unique")

    classes = tuple(
        sorted(
            {
                item.class_id
                for frame in saved_frames
                for item in (*frame.predictions, *frame.targets)
            }
        )
    )
    metrics = {
        threshold: {
            class_id: _evaluate_class(saved_frames, class_id, threshold)
            for class_id in classes
        }
        for threshold in thresholds
    }

    valid_average_precisions = [
        class_metrics.average_precision
        for threshold_metrics in metrics.values()
        for class_metrics in threshold_metrics.values()
        if class_metrics.average_precision is not None
    ]
    map_50_values = [
        class_metrics.average_precision
        for class_metrics in metrics.get(0.5, {}).values()
        if class_metrics.average_precision is not None
    ]

    return DetectionEvaluation(
        iou_thresholds=thresholds,
        classes=classes,
        metrics=metrics,
        mean_average_precision=(
            fsum(valid_average_precisions) / len(valid_average_precisions)
            if valid_average_precisions
            else None
        ),
        mean_average_precision_50=(
            fsum(map_50_values) / len(map_50_values) if map_50_values else None
        ),
    )
