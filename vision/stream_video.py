"""Causal CPU video observations and a bounded, sampled visual-memory buffer.

This Watch frontend combines a pretrained ImageNet visual encoder, optional COCO
object detection, OpenCV background subtraction and short-lived region tracking.
Feature changes and object labels can prioritize attention but do not establish
events or intent. A semantic reviewer must inspect the actual JPEG sequence. The
explicit ``appearance`` encoder uses handcrafted descriptors only.

Every decoded frame contributes change signals. With ``on_frame`` supplied, every
decoded frame is JPEG encoded and delivered to that callback for bounded rolling
retention; only ``sample_fps`` observations/second run learned models and advance
the attention controller. Without a callback, only sampled frames are encoded.
JPEG retention has original frame cadence but omits audio and may resize/recompress
imagery. No future frame is used to construct a current observation.

Video decoding requires the optional ``video`` dependencies: NumPy and OpenCV.
Imports remain lazy so the controller and buffer can run without a video runtime.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class FrameObservation:
    """A causal observation in media seconds, with a sampled JPEG for reviewers.

    Motion and appearance_change summarize maxima since the preceding emitted
    sample. ``uncertainty`` is an image-quality/change heuristic, not a calibrated
    probability. Tracks distinguish generic foreground regions from COCO objects.
    """

    time: float
    frame_index: int
    jpeg: bytes
    motion: float = 0.0
    appearance_change: float = 0.0
    uncertainty: float = 0.0
    tracks: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class VideoMetadata:
    width: int
    height: int
    fps: float
    duration: float | None
    frame_count: int | None
    sample_fps: float
    encoder: str = "appearance"
    detector: str = "none"
    buffer_kind: str = "sampled_jpeg"


class FrameRingBuffer:
    """Bound recent images by media age, JPEG bytes, and frame count.

    ``max_bytes`` measures encoded JPEG payload, not Python object overhead. The
    additional frame-count limit bounds metadata overhead. An image larger than
    the complete byte budget is dropped, rather than breaking the memory limit.
    Lookups are inclusive and reject invalid/future ranges supplied as ``now``.
    """

    def __init__(
        self,
        duration_seconds: float = 60.0,
        max_bytes: int = 32 * 1024 * 1024,
        max_frames: int = 1800,
    ) -> None:
        if not math.isfinite(duration_seconds) or duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive and finite")
        if max_bytes <= 0 or max_frames <= 0:
            raise ValueError("max_bytes and max_frames must be positive")
        self.duration_seconds = float(duration_seconds)
        self.max_bytes = int(max_bytes)
        self.max_frames = int(max_frames)
        self._frames: deque[FrameObservation] = deque()
        self._bytes = 0
        self._latest_time: float | None = None
        self.dropped_frames = 0

    def __len__(self) -> int:
        return len(self._frames)

    @property
    def byte_size(self) -> int:
        return self._bytes

    @property
    def oldest_time(self) -> float | None:
        return self._frames[0].time if self._frames else None

    @property
    def newest_time(self) -> float | None:
        return self._frames[-1].time if self._frames else None

    def append(self, observation: FrameObservation) -> bool:
        if not math.isfinite(observation.time) or observation.time < 0:
            raise ValueError("observation time must be finite and nonnegative")
        if self._latest_time is not None and observation.time <= self._latest_time:
            raise ValueError("observations must have strictly increasing timestamps")
        if not isinstance(observation.jpeg, bytes) or not observation.jpeg:
            raise ValueError("an observation must carry a nonempty JPEG byte payload")
        self._latest_time = observation.time
        self.prune(observation.time)
        if len(observation.jpeg) > self.max_bytes:
            self.dropped_frames += 1
            return False
        self._frames.append(observation)
        self._bytes += len(observation.jpeg)
        while self._bytes > self.max_bytes or len(self._frames) > self.max_frames:
            self._drop_oldest()
        return True

    def _drop_oldest(self) -> None:
        self._bytes -= len(self._frames.popleft().jpeg)
        self.dropped_frames += 1

    def prune(self, now: float) -> None:
        if not math.isfinite(now):
            raise ValueError("now must be finite")
        cutoff = now - self.duration_seconds
        while self._frames and self._frames[0].time < cutoff:
            self._drop_oldest()

    def select(
        self, start: float, end: float, max_frames: int = 16
    ) -> list[FrameObservation]:
        """Select a time-spaced sequence from already observed, inclusive times.

        Always includes both available endpoints when max_frames >= 2. For one
        frame, returns the most recent available sample. No image is duplicated.
        This selection cannot recover evidence already evicted by retention.
        """
        if not math.isfinite(start) or not math.isfinite(end) or end < start:
            raise ValueError("selection needs finite start <= end")
        if max_frames < 1:
            raise ValueError("max_frames must be at least one")
        candidates = [item for item in self._frames if start <= item.time <= end]
        if len(candidates) <= max_frames:
            return candidates
        if max_frames == 1:
            return [candidates[-1]]
        # Even index spacing avoids duplicates and preserves the latest evidence.
        # Observations themselves are sampled approximately uniformly in time.
        return [
            candidates[round(index * (len(candidates) - 1) / (max_frames - 1))]
            for index in range(max_frames)
        ]

    def snapshot(
        self, now: float, lookback: float = 10.0, max_frames: int = 16
    ) -> list[FrameObservation]:
        """Get a causal request sequence ending at ``now`` (never later)."""
        if not math.isfinite(lookback) or lookback < 0:
            raise ValueError("lookback must be finite and nonnegative")
        return self.select(now - lookback, now, max_frames)


def _load_video_dependencies() -> tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as error:
        raise RuntimeError(
            "Video replay requires NumPy and OpenCV. Install the project's video "
            "extra in a virtual environment: python -m pip install -e '.[stream]'"
        ) from error
    return cv2, np


def _iou(first: list[float], second: list[float]) -> float:
    width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    intersection = width * height
    first_area = (first[2] - first[0]) * (first[3] - first[1])
    second_area = (second[2] - second[0]) * (second[3] - second[1])
    union = first_area + second_area - intersection
    return intersection / union if union > 0 else 0.0


class _MotionTracker:
    """Greedy short-lived generic-region tracker; no identity guarantees."""

    def __init__(self, limit: int = 24, max_age: float = 2.0, prefix: str = "motion") -> None:
        self.limit = limit
        self.max_age = max_age
        self.prefix = prefix
        self._tracks: dict[int, dict[str, Any]] = {}
        self._next_id = 1

    def update(
        self,
        boxes: list[list[float]],
        now: float,
        labels: list[str] | None = None,
        scores: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        self._tracks = {
            key: item
            for key, item in self._tracks.items()
            if now - item["last_seen"] <= self.max_age
        }
        remaining = set(self._tracks)
        for index, box in enumerate(boxes[: self.limit]):
            label = labels[index] if labels else "motion_region"
            score = scores[index] if scores else None
            center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            choices: list[tuple[float, int]] = []
            for key in remaining:
                previous = self._tracks[key]
                if previous["label"] != label:
                    continue
                old = previous["bbox"]
                distance = math.hypot(
                    center[0] - (old[0] + old[2]) / 2,
                    center[1] - (old[1] + old[3]) / 2,
                )
                overlap = _iou(box, old)
                if overlap >= 0.1 or distance <= 0.08:
                    choices.append((overlap - distance, key))
            if choices:
                _, key = max(choices)
                remaining.remove(key)
                track = self._tracks[key]
                elapsed = max(now - track["last_seen"], 1e-6)
                old = track["bbox"]
                velocity = [
                    (center[0] - (old[0] + old[2]) / 2) / elapsed,
                    (center[1] - (old[1] + old[3]) / 2) / elapsed,
                ]
                track.update(bbox=box, last_seen=now, velocity=velocity, detector_score=score)
            elif len(self._tracks) < self.limit:
                key = self._next_id
                self._next_id += 1
                self._tracks[key] = {
                    "id": f"{self.prefix}-{key}",
                    "label": label,
                    "source": self.prefix,
                    "detector_score": score,
                    "bbox": box,
                    "first_seen": now,
                    "last_seen": now,
                    "velocity": [0.0, 0.0],
                }
        return [
            {
                **item,
                "bbox": list(item["bbox"]),
                "velocity": list(item["velocity"]),
                "age_seconds": now - item["first_seen"],
                "visible": item["last_seen"] == now,
                "missing_seconds": now - item["last_seen"],
            }
            for _, item in sorted(self._tracks.items())
        ]


class _MobileNetEncoder:
    """Frozen pretrained image features, not an event classifier.

    Only locally cached official torchvision weights are loaded. A replay never
    downloads model files implicitly. The CLI's explicit model preparation step
    owns obtaining these weights and associated network access.
    """

    def __init__(self, cache: str | Path) -> None:
        try:
            import torch
            from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small
        except ImportError as error:
            raise RuntimeError(
                "The mobilenet encoder requires torch and torchvision. Install "
                "the project's stream extra, or explicitly use encoder='appearance'."
            ) from error
        weights = MobileNet_V3_Small_Weights.DEFAULT
        checkpoint = Path(cache) / "hub" / "checkpoints" / Path(urlparse(weights.url).path).name
        if not checkpoint.is_file():
            raise RuntimeError(
                f"Pretrained visual encoder weights are missing: {checkpoint}. "
                "Run the explicit model preparation command first. Replay will "
                "not download weights automatically."
            )
        self.torch = torch
        torch.set_num_threads(min(torch.get_num_threads(), 2))
        model = mobilenet_v3_small(weights=None)
        model.load_state_dict(torch.load(checkpoint, map_location="cpu", weights_only=True))
        model.eval()
        self.features = model.features
        self.pool = model.avgpool
        self.transform = weights.transforms()
        self.previous: Any = None
        self.baseline: Any = None

    def change(self, frame: Any, quiet: bool) -> float:
        # numpy BGR frame -> contiguous RGB tensor; transforms apply the exact
        # normalization/resize associated with the cached pretrained weights.
        tensor = self.torch.from_numpy(frame[:, :, ::-1].copy()).permute(2, 0, 1)
        with self.torch.inference_mode():
            features = self.pool(self.features(self.transform(tensor).unsqueeze(0))).flatten(1)
            embedding = self.torch.nn.functional.normalize(features, dim=1)
            if self.previous is None:
                change = 0.0
                self.baseline = embedding.clone()
            else:
                immediate = max(0.0, 1.0 - float((embedding * self.previous).sum()))
                sustained = max(0.0, 1.0 - float((embedding * self.baseline).sum()))
                change = min(1.0, max(immediate * 4, sustained * 2))
                # The baseline follows quiet scenes slowly; it is never an
                # event label or an automatic declaration that a scene is safe.
                if quiet:
                    self.baseline = self.torch.nn.functional.normalize(
                        self.baseline * 0.98 + embedding * 0.02, dim=1
                    )
            self.previous = embedding
        return change


class _SSDLiteDetector:
    """Cached pretrained COCO detections; object scores are not event confidence."""

    def __init__(self, cache: str | Path, score_threshold: float) -> None:
        try:
            import torch
            from torchvision.models.detection import (
                SSDLite320_MobileNet_V3_Large_Weights,
                ssdlite320_mobilenet_v3_large,
            )
        except ImportError as error:
            raise RuntimeError(
                "The ssdlite detector requires the project's stream extra. "
                "Install it, or explicitly use detector='none'."
            ) from error
        weights = SSDLite320_MobileNet_V3_Large_Weights.DEFAULT
        checkpoint = Path(cache) / "hub" / "checkpoints" / Path(urlparse(weights.url).path).name
        if not checkpoint.is_file():
            raise RuntimeError(
                f"Pretrained COCO detector weights are missing: {checkpoint}. "
                "Run the explicit model preparation command first."
            )
        torch.set_num_threads(min(torch.get_num_threads(), 2))
        self.torch = torch
        self.model = ssdlite320_mobilenet_v3_large(weights=None, weights_backbone=None)
        self.model.load_state_dict(torch.load(checkpoint, map_location="cpu", weights_only=True))
        self.model.eval()
        self.transform = weights.transforms()
        self.categories = weights.meta["categories"]
        self.threshold = score_threshold

    def detect(self, frame: Any) -> tuple[list[list[float]], list[str], list[float]]:
        height, width = frame.shape[:2]
        tensor = self.torch.from_numpy(frame[:, :, ::-1].copy()).permute(2, 0, 1)
        with self.torch.inference_mode():
            result = self.model([self.transform(tensor)])[0]
        boxes: list[list[float]] = []
        labels: list[str] = []
        scores: list[float] = []
        for box, label, score in zip(result["boxes"], result["labels"], result["scores"]):
            value = float(score)
            if value < self.threshold or len(boxes) >= 16:
                continue
            x1, y1, x2, y2 = box.tolist()
            boxes.append([
                max(0.0, min(1.0, x1 / width)), max(0.0, min(1.0, y1 / height)),
                max(0.0, min(1.0, x2 / width)), max(0.0, min(1.0, y2 / height)),
            ])
            labels.append(self.categories[int(label)])
            scores.append(value)
        return boxes, labels, scores


class VideoSource:
    """Decode a video causally and emit bounded-size JPEG observations.

    ``sample_fps`` governs reviewer imagery, not motion processing: cheap change
    calculations run on every decoded frame at <=320px width. Source timestamps
    use decoder presentation time, falling back to frame_index/fps when absent.
    This class does not sleep; the replay engine controls wall-clock pacing.
    """

    def __init__(
        self,
        path: str | Path,
        sample_fps: float = 5.0,
        jpeg_quality: int = 75,
        max_dimension: int = 960,
        encoder: str = "mobilenet",
        model_cache: str | Path = "local/models",
        detector: str = "ssdlite",
        detector_score: float = 0.45,
        on_frame: Callable[[FrameObservation], None] | None = None,
    ) -> None:
        if not math.isfinite(sample_fps) or not 0 < sample_fps <= 60:
            raise ValueError("sample_fps must be between 0 and 60")
        if not 1 <= jpeg_quality <= 100 or max_dimension < 32:
            raise ValueError("JPEG quality must be 1..100; max_dimension >= 32")
        if encoder not in {"mobilenet", "appearance"}:
            raise ValueError("encoder must be 'mobilenet' or 'appearance'")
        if detector not in {"ssdlite", "none"}:
            raise ValueError("detector must be 'ssdlite' or 'none'")
        if not math.isfinite(detector_score) or not 0 < detector_score <= 1:
            raise ValueError("detector_score must be between 0 and 1")
        self.path = str(path)
        self.sample_fps = float(sample_fps)
        self.jpeg_quality = int(jpeg_quality)
        self.max_dimension = int(max_dimension)
        self.encoder = encoder
        self.model_cache = Path(model_cache)
        self.detector = detector
        self.detector_score = detector_score
        self.on_frame = on_frame
        self._capture: Any = None
        self._metadata: VideoMetadata | None = None
        self._started = False
        self.timestamp_fallback_count = 0

    def open(self) -> VideoSource:
        if self._capture is not None:
            return self
        cv2, _ = _load_video_dependencies()
        capture = cv2.VideoCapture(self.path)
        if not capture.isOpened():
            capture.release()
            raise ValueError(f"Cannot decode video source: {self.path}")
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        count_raw = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if width <= 0 or height <= 0:
            capture.release()
            raise ValueError("Video source has no readable image dimensions")
        if not math.isfinite(fps) or fps <= 0:
            fps = 25.0
        frame_count = int(count_raw) if math.isfinite(count_raw) and count_raw > 0 else None
        self._metadata = VideoMetadata(
            width=width,
            height=height,
            fps=fps,
            duration=frame_count / fps if frame_count else None,
            frame_count=frame_count,
            sample_fps=min(self.sample_fps, fps),
            encoder=self.encoder,
            detector=self.detector,
            buffer_kind="all_frames_jpeg" if self.on_frame is not None else "sampled_jpeg",
        )
        self._capture = capture
        return self

    @property
    def metadata(self) -> VideoMetadata:
        if self._metadata is None:
            self.open()
        assert self._metadata is not None
        return self._metadata

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def __enter__(self) -> VideoSource:
        return self.open()

    def __exit__(self, *_: object) -> None:
        self.close()

    def __iter__(self) -> Iterator[FrameObservation]:
        if self._started:
            raise RuntimeError("VideoSource supports a single replay; create a new source to restart")
        self.open()
        self._started = True
        cv2, np = _load_video_dependencies()
        metadata = self.metadata
        try:
            learned_encoder = _MobileNetEncoder(self.model_cache) if self.encoder == "mobilenet" else None
            object_detector = (
                _SSDLiteDetector(self.model_cache, self.detector_score)
                if self.detector == "ssdlite" else None
            )
        except Exception:
            self.close()
            raise
        background = cv2.createBackgroundSubtractorMOG2(
            history=max(30, round(metadata.fps * 10)), varThreshold=24, detectShadows=True
        )
        tracker = _MotionTracker()
        object_tracker = _MotionTracker(limit=16, prefix="coco")
        previous_gray = None
        previous_thumbnail = None
        previous_hist = None
        previous_time = -1.0
        frame_index = -1
        emitted_index = -1
        next_sample = 0.0
        peak_motion = peak_appearance = peak_uncertainty = 0.0
        latest: tuple[Any, float, list[dict[str, Any]], bytes | None] | None = None

        def encode(frame: Any) -> tuple[Any, bytes]:
            height, width = frame.shape[:2]
            scale = min(1.0, self.max_dimension / max(width, height))
            if scale < 1:
                frame = cv2.resize(
                    frame, (max(1, round(width * scale)), max(1, round(height * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            success, encoded = cv2.imencode(
                ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality]
            )
            if not success:
                raise RuntimeError(f"Could not JPEG encode frame {frame_index}")
            return frame, encoded.tobytes()

        def emit(
            frame: Any, now: float, tracks: list[dict[str, Any]], jpeg: bytes | None = None
        ) -> FrameObservation:
            nonlocal peak_motion, peak_appearance, peak_uncertainty, emitted_index
            if jpeg is None:
                frame, jpeg = encode(frame)
            if object_detector is not None:
                boxes, labels, scores = object_detector.detect(frame)
                object_tracks = object_tracker.update(boxes, now, labels, scores)
                tracks = tracks + object_tracks
                if any(item["missing_seconds"] > 0.25 for item in object_tracks):
                    peak_uncertainty = max(peak_uncertainty, 0.55)
            result = FrameObservation(
                time=now, frame_index=frame_index, jpeg=jpeg,
                motion=peak_motion,
                appearance_change=max(
                    peak_appearance,
                    learned_encoder.change(frame, quiet=peak_motion < 0.025)
                    if learned_encoder else 0.0,
                ),
                uncertainty=peak_uncertainty, tracks=tracks,
            )
            peak_motion = peak_appearance = peak_uncertainty = 0.0
            emitted_index = frame_index
            return result

        try:
            while True:
                ok, frame = self._capture.read()
                if not ok:
                    break
                frame_index += 1
                raw_time = float(self._capture.get(cv2.CAP_PROP_POS_MSEC)) / 1000
                if not math.isfinite(raw_time) or raw_time < 0 or raw_time <= previous_time:
                    raw_time = max(frame_index / metadata.fps, previous_time + 1 / metadata.fps)
                    self.timestamp_fallback_count += 1
                now = raw_time
                previous_time = now
                height, width = frame.shape[:2]
                work_scale = min(1.0, 320 / max(width, height))
                small = cv2.resize(
                    frame,
                    (max(1, round(width * work_scale)), max(1, round(height * work_scale))),
                    interpolation=cv2.INTER_AREA,
                )
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                blurred = cv2.GaussianBlur(gray, (5, 5), 0)
                foreground = background.apply(small)
                mask = np.where(foreground == 255, 255, 0).astype(np.uint8)
                mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
                # MOG2 initializes the complete first frame as foreground.
                if frame_index == 0:
                    mask[:] = 0
                changed = (
                    float(np.count_nonzero(cv2.absdiff(blurred, previous_gray) > 20)) / gray.size
                    if previous_gray is not None else 0.0
                )
                foreground_area = float(np.count_nonzero(mask)) / gray.size
                motion = max(changed, foreground_area)
                thumbnail = cv2.resize(small, (32, 18), interpolation=cv2.INTER_AREA).astype(np.float32)
                hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
                histogram = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256])
                histogram /= max(float(histogram.sum()), 1.0)
                if previous_thumbnail is None:
                    appearance = 0.0
                else:
                    pixel_drift = float(np.abs(thumbnail - previous_thumbnail).mean()) / 64
                    histogram_drift = float(np.abs(histogram - previous_hist).sum()) / 2
                    appearance = min(1.0, 0.65 * pixel_drift + 0.35 * histogram_drift)
                exposure = float(np.count_nonzero((gray < 12) | (gray > 245))) / gray.size
                laplacian = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                blur = max(0.0, 1.0 - laplacian / 30) * 0.5
                uncertainty = min(1.0, max(exposure, blur, foreground_area * 0.75))
                contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                components = sorted(contours, key=cv2.contourArea, reverse=True)
                boxes = []
                for contour in components[:48]:
                    area = cv2.contourArea(contour) / gray.size
                    if area < 0.0015 or area > 0.75:
                        continue
                    x, y, box_width, box_height = cv2.boundingRect(contour)
                    boxes.append([
                        x / gray.shape[1], y / gray.shape[0],
                        (x + box_width) / gray.shape[1], (y + box_height) / gray.shape[0],
                    ])
                tracks = tracker.update(boxes, now)
                if any(item["missing_seconds"] > 0.25 for item in tracks):
                    uncertainty = max(uncertainty, 0.55)
                peak_motion = max(peak_motion, motion)
                peak_appearance = max(peak_appearance, appearance)
                peak_uncertainty = max(peak_uncertainty, uncertainty)
                previous_gray = blurred
                previous_thumbnail = thumbnail
                previous_hist = histogram
                encoded = None
                if self.on_frame is not None:
                    frame, encoded = encode(frame)
                    self.on_frame(FrameObservation(
                        time=now, frame_index=frame_index, jpeg=encoded,
                        motion=motion, appearance_change=appearance,
                        uncertainty=uncertainty, tracks=tracks,
                    ))
                latest = frame, now, tracks, encoded
                if now + 1e-9 >= next_sample:
                    yield emit(frame, now, tracks, encoded)
                    next_sample = (math.floor(now * metadata.sample_fps + 1e-8) + 1) / metadata.sample_fps
            if latest is not None and frame_index != emitted_index:
                yield emit(*latest)
            if frame_index < 0:
                raise ValueError("Video source contains no decodable frames")
        finally:
            self.close()
