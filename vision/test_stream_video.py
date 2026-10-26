"""Memory-retention and actual encoded-video checks for the Watch frontend."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from vision.stream_video import FrameObservation, FrameRingBuffer, VideoSource


class FrameRingBufferTests(unittest.TestCase):
    def observation(self, now: float, size: int = 4) -> FrameObservation:
        return FrameObservation(time=now, frame_index=round(now * 10), jpeg=b"x" * size)

    def test_retention_limits_and_oversized_frame_drop(self) -> None:
        ring = FrameRingBuffer(duration_seconds=2, max_bytes=10, max_frames=3)
        ring.append(self.observation(0))
        ring.append(self.observation(1))
        ring.append(self.observation(2))
        self.assertEqual(ring.oldest_time, 1)
        self.assertLessEqual(ring.byte_size, 10)
        ring.append(self.observation(4))
        self.assertEqual(ring.oldest_time, 2)
        self.assertFalse(ring.append(self.observation(5, size=11)))
        self.assertEqual([item.time for item in ring.select(0, 5)], [4])
        self.assertLessEqual(ring.byte_size, 10)

    def test_causal_selection_and_endpoint_preservation(self) -> None:
        ring = FrameRingBuffer()
        for now in range(10):
            ring.append(self.observation(float(now)))
        chosen = ring.snapshot(now=7, lookback=5, max_frames=3)
        self.assertEqual([item.time for item in chosen], [2, 4, 7])
        self.assertEqual(ring.snapshot(3, 3, 1)[0].time, 3)
        self.assertTrue(all(item.time <= 7 for item in chosen))
        self.assertEqual(ring.snapshot(-1, 1), [])
        with self.assertRaises(ValueError):
            ring.append(self.observation(8))

    def test_count_limit_bounds_metadata_with_tiny_payloads(self) -> None:
        ring = FrameRingBuffer(duration_seconds=60, max_bytes=1000, max_frames=2)
        for now in range(5):
            ring.append(self.observation(now, size=1))
        self.assertEqual(len(ring), 2)
        self.assertEqual(ring.oldest_time, 3)


class VideoSourceTests(unittest.TestCase):
    def test_actual_video_retains_between_sample_motion_and_valid_jpegs(self) -> None:
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("optional video runtime is not installed")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "motion.avi"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 20, (160, 96))
            self.assertTrue(writer.isOpened(), "OpenCV needs the MJPEG video writer for this check")
            for index in range(40):
                # Textured quiet scene followed by a brief motion event entirely
                # between the samples at 0.5 and 0.75 seconds.
                image = np.full((96, 160, 3), 100, dtype=np.uint8)
                cv2.line(image, (0, 48), (159, 48), (130, 130, 130), 2)
                if index in (12, 13):
                    cv2.rectangle(image, (60, 20), (100, 70), (230, 230, 230), -1)
                writer.write(image)
            writer.release()
            ring = FrameRingBuffer()
            with VideoSource(
                path, sample_fps=4, max_dimension=96, encoder="appearance", detector="none",
                on_frame=ring.append,
            ) as source:
                metadata = source.metadata
                observations = list(source)
            self.assertEqual((metadata.width, metadata.height), (160, 96))
            self.assertEqual(metadata.buffer_kind, "all_frames_jpeg")
            self.assertEqual(len(ring), 40)
            self.assertAlmostEqual(metadata.duration or 0, 2.0)
            self.assertGreaterEqual(len(observations), 8)
            self.assertLessEqual(len(observations), 10)
            self.assertTrue(all(
                first.time < second.time and first.frame_index < second.frame_index
                for first, second in zip(observations, observations[1:])
            ))
            event_samples = [item for item in observations if 0.7 <= item.time <= 0.8]
            self.assertEqual(len(event_samples), 1)
            self.assertGreater(event_samples[0].motion, 0.05)
            # The transient object is absent at controller sample time .75s,
            # but callback retention preserves actual .6/.65s image evidence.
            between_samples = ring.select(0.59, 0.66)
            self.assertEqual(len(between_samples), 2)
            object_image = cv2.imdecode(
                np.frombuffer(between_samples[0].jpeg, dtype=np.uint8), cv2.IMREAD_COLOR
            )
            self.assertGreater(float(object_image[20:35, 40:55].mean()), 180)
            for observation in observations:
                decoded = cv2.imdecode(np.frombuffer(observation.jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                self.assertIsNotNone(decoded)
                self.assertLessEqual(max(decoded.shape[:2]), 96)
                for value in (observation.motion, observation.appearance_change, observation.uncertainty):
                    self.assertGreaterEqual(value, 0)
                    self.assertLessEqual(value, 1)
                self.assertLessEqual(len(observation.tracks), 24)
                self.assertTrue(all(item["label"] == "motion_region" for item in observation.tracks))


if __name__ == "__main__":
    unittest.main()
