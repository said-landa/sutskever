"""Live capture checks with synthetic frames and fixture HTTP; no camera or charges."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from vision.stream_attention import AttentionConfig
from vision.stream_live import LiveExperiment, LiveVideoSource, _LatestCapture, camera_url, main
from vision.stream_models import AlibabaVisionClient, ModelConfig


class CameraAddressTests(unittest.TestCase):
    def test_credentials_are_encoded_and_only_private_camera_ips_allowed(self):
        url = camera_url('192.168.1.50', 'camera@user', 'p:/?# word', 'stream2')
        self.assertEqual(url, 'rtsp://camera%40user:p%3A%2F%3F%23%20word@192.168.1.50:554/stream2')
        for host in ('8.8.8.8', '127.0.0.1', '0.0.0.0', '169.254.1.2', '224.0.0.1', 'host/stream1'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                camera_url(host, 'camera', 'password', 'stream2')

    def test_invalid_args_do_not_prompt_or_connect(self):
        with patch('builtins.input') as prompt, patch('getpass.getpass') as secret:
            with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                main(['--host', '192.168.1.50', '--seconds', 'nan'])
            prompt.assert_not_called()
            secret.assert_not_called()


class LatestCaptureTests(unittest.TestCase):
    def test_slow_consumer_gets_newest_frame_and_disconnect_is_not_normal_eof(self):
        class CV:
            CAP_PROP_POS_MSEC = 0

        class Native:
            def __init__(self):
                self.count = 0
                self.released = False

            def read(self):
                self.count += 1
                return (True, self.count) if self.count <= 50 else (False, None)

            def release(self):
                self.released = True

        native = Native()
        capture = _LatestCapture(native, CV, 60)
        # Start producer explicitly so consumption waits until all 50 frames arrive.
        capture.started = time.monotonic()
        capture.worker = threading.Thread(target=capture._produce)
        capture.worker.start()
        capture.worker.join(timeout=2)
        self.assertEqual(capture.read(), (True, 50))
        self.assertEqual(capture.metrics()['overwritten_frames'], 49)
        self.assertEqual(capture.metrics()['frame_queue_capacity'], 1)
        self.assertTrue(native.released)
        with self.assertRaisesRegex(RuntimeError, 'stream ended'):
            capture.read()
        capture.release()

    def test_failed_open_never_exposes_credential_bearing_url(self):
        class CV:
            CAP_FFMPEG = 1
            CAP_PROP_OPEN_TIMEOUT_MSEC = 2
            CAP_PROP_READ_TIMEOUT_MSEC = 3

            def setLogLevel(self, level):
                pass

            def VideoCapture(self, url, *args):
                raise RuntimeError(url)

        secret_url = camera_url('192.168.1.50', 'unique-user', 'unique-secret', 'stream2')
        with patch('vision.stream_live._load_video_dependencies', return_value=(CV(), None)):
            with self.assertRaises(RuntimeError) as raised:
                LiveVideoSource(secret_url).open()
        self.assertNotIn('unique', str(raised.exception))
        self.assertNotIn('rtsp:', str(raised.exception))


class LiveIntegrationTests(unittest.TestCase):
    def test_actual_images_to_fixture_qwen_usage_and_duration_limit(self):
        import cv2
        import numpy as np

        class Native:
            def isOpened(self):
                return True

            def get(self, prop):
                return {cv2.CAP_PROP_FRAME_WIDTH: 160, cv2.CAP_PROP_FRAME_HEIGHT: 96,
                        cv2.CAP_PROP_FPS: 50}[prop]

            def read(self):
                time.sleep(0.005)
                return True, np.full((96, 160, 3), 100, dtype=np.uint8)

            def release(self):
                pass

        requests = []

        def transport(request, timeout, limit):
            requests.append(json.loads(request.data))
            return json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
                'action': 'observe', 'evidence': 'Fixture quiet scene.',
                'event_summary': 'Fixture ordinary activity.', 'recheck_after_seconds': None})}}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110}}).encode()

        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'ALIBABA_API_KEY': 'fixture-key'}), \
                patch.object(cv2, 'VideoCapture', return_value=Native()), redirect_stdout(io.StringIO()):
            source = LiveVideoSource('rtsp://unique-user:unique-secret@192.168.1.50/stream2', seconds=0.2,
                                     sample_fps=30, encoder='appearance', detector='none')
            experiment = LiveExperiment('Describe visible changes.', Path(folder) / 'run',
                client=AlibabaVisionClient(ModelConfig(), transport=transport), realtime=True,
                config=AttentionConfig(startup_seconds=0.01, max_calls=3))
            experiment.live_source = source
            source.on_frame = experiment.buffer_frame
            with source:
                summary = experiment.run(source)
            self.assertEqual(summary['status'], 'completed')
            self.assertEqual(summary['live']['capture']['end_reason'], 'duration_limit')
            self.assertGreater(summary['sampled_observations'], 1)
            self.assertLessEqual(len(requests), 3)
            self.assertGreater(len(requests), 0)
            self.assertEqual(summary['live']['submitted_calls'], len(requests))
            self.assertEqual(summary['usage']['input_tokens'], 100 * len(requests))
            self.assertIsNone(summary['estimated_cost_usd'])
            self.assertGreater(summary['live']['submitted_jpeg_bytes'], 0)
            saved = '\n'.join(path.read_text() for path in experiment.output.iterdir())
            for secret in ('unique-user', 'unique-secret', 'fixture-key', 'rtsp://', 'data:image'):
                self.assertNotIn(secret, saved)
            self.assertTrue(all(event['observed_until'] <= summary['media_seconds_observed']
                                for event in experiment.events if event['type'] == 'request'))

    def test_cli_alibaba_key_and_camera_credentials_remain_local(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.dict(os.environ, {'TAPO_CAMERA_USERNAME': 'fixture-user',
                                        'TAPO_CAMERA_PASSWORD': 'fixture-password'}, clear=True), \
                patch('getpass.getpass', return_value='fixture-key'), \
                patch('vision.stream_live.LiveVideoSource.open', side_effect=RuntimeError('Fixture unavailable')), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--host', '192.168.1.50', '--backend', 'alibaba', '--ask-key',
                                   '--output', str(Path(folder) / 'run')]), 1)
            self.assertNotIn('ALIBABA_API_KEY', os.environ)


if __name__ == '__main__':
    unittest.main()
