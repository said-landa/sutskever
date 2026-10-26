"""A bounded Tapo RTSP experiment using the existing Watch / Attend / Inspect loop.

Run: .venv/bin/python -m vision.stream_live --host CAMERA_IP
Add --backend alibaba --ask-key to send selected images to Qwen automatically.
Camera credentials are prompted locally, never passed as command arguments.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import getpass
import ipaddress
import json
import math
import os
from pathlib import Path
import resource
import threading
import time
from urllib.parse import quote

from vision.stream_attention import AttentionConfig
from vision.stream_models import AlibabaVisionClient, ModelConfig, ModelPrices
from vision.stream_replay import ReplayExperiment
from vision.stream_video import VideoMetadata, VideoSource, _load_video_dependencies


def camera_url(host: str, username: str, password: str, stream: str) -> str:
    """Require a LAN IP, and encode reserved characters in camera credentials."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError('Use the camera local IP from Tapo Device Info') from None
    if not address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast:
        raise ValueError('Use a private LAN camera IP')
    if not username or not password or stream not in ('stream1', 'stream2'):
        raise ValueError('Camera account and stream1/stream2 are required')
    literal = f'[{address}]' if address.version == 6 else str(address)
    return f'rtsp://{quote(username, safe="")}:{quote(password, safe="")}@{literal}:554/{stream}'


class _LatestCapture:
    """Drain the decoder on a worker; keep ONE frame rather than a growing queue.

    Timestamps measure local decode arrival, not the camera sensor exposure time.
    Native read/open timeouts bound disconnect waits. No reconnect or retry occurs.
    """
    def __init__(self, capture, cv2, seconds: float):
        self.capture, self.cv2, self.seconds = capture, cv2, seconds
        self.condition = threading.Condition()
        self.stop = threading.Event()
        self.worker = None
        self.slot = None
        self.timestamp = 0.0
        self.decoded_frames = self.overwritten_frames = self.delivered_frames = 0
        self.finished = False
        self.end_reason = None
        self.error = None
        self.started = 0.0

    def _produce(self):
        try:
            while not self.stop.is_set() and time.monotonic() - self.started < self.seconds:
                ok, frame = self.capture.read()
                now = time.monotonic() - self.started
                if now >= self.seconds:
                    break
                if not ok:
                    self.error = 'Camera stream ended or timed out; check power, Wi-Fi and camera account'
                    break
                with self.condition:
                    self.decoded_frames += 1
                    if self.slot is not None:
                        self.overwritten_frames += 1
                    self.slot = (now, frame)
                    self.condition.notify_all()
            self.end_reason = 'disconnected' if self.error else ('stopped' if self.stop.is_set() else 'duration_limit')
        except Exception:
            self.error = 'Camera decoder failed; check the local camera connection'
            self.end_reason = 'decoder_error'
        finally:
            self.capture.release()
            with self.condition:
                self.finished = True
                self.condition.notify_all()

    def read(self):
        if self.worker is None:
            self.started = time.monotonic()
            self.worker = threading.Thread(target=self._produce, daemon=True, name='tapo-decoder')
            self.worker.start()
        with self.condition:
            ready = self.condition.wait_for(lambda: self.slot is not None or self.finished, timeout=6)
            if not ready:
                raise RuntimeError('Camera read timed out; no fresh frame is available')
            if self.slot is None:
                if self.error:
                    raise RuntimeError(self.error) from None
                return False, None
            self.timestamp, frame = self.slot
            self.slot = None
            self.delivered_frames += 1
            return True, frame

    def get(self, prop):
        if prop == self.cv2.CAP_PROP_POS_MSEC:
            return self.timestamp * 1000
        raise ValueError('Live capture only exposes reception timestamps after opening')

    def release(self):
        self.stop.set()
        if self.worker is None:
            self.capture.release()
        else:
            # Only the producer releases native capture; never race a blocked read.
            self.worker.join(timeout=6)

    def metrics(self):
        return dict(decoded_frames=self.decoded_frames, delivered_frames=self.delivered_frames,
                    overwritten_frames=self.overwritten_frames, end_reason=self.end_reason,
                    timestamp_kind='local_decode_arrival', frame_queue_capacity=1)


class LiveVideoSource(VideoSource):
    def __init__(self, url: str, *, seconds: float = 60, **kwargs):
        if not math.isfinite(seconds) or not 0 < seconds <= 3600:
            raise ValueError('Experiment duration must be between zero and 3600 seconds')
        super().__init__('live-camera', **kwargs)
        self._url, self.seconds, self.live_capture = url, seconds, None

    def open(self):
        if self._capture is not None:
            return self
        # Set before cv2 imports its FFmpeg backend; native diagnostics can contain
        # credential-bearing URLs. Python errors below are deliberately generic.
        os.environ['OPENCV_FFMPEG_LOGLEVEL'] = '-8'
        os.environ['OPENCV_LOG_LEVEL'] = 'SILENT'
        os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp'
        cv2, _ = _load_video_dependencies()
        if callable(getattr(cv2, 'setLogLevel', None)):
            cv2.setLogLevel(0)
        try:
            capture = cv2.VideoCapture(self._url, cv2.CAP_FFMPEG, [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000,
            ])
        except Exception:
            raise RuntimeError('Could not open camera with the FFmpeg RTSP backend') from None
        if not capture.isOpened():
            capture.release()
            raise RuntimeError('Could not open camera; check LAN IP and the dedicated Camera Account')
        width, height = (int(capture.get(prop)) for prop in (cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        if width <= 0 or height <= 0:
            capture.release()
            raise RuntimeError('Camera opened without readable image dimensions')
        if not math.isfinite(fps) or fps <= 0:
            fps = 25.0
        self._metadata = VideoMetadata(width, height, fps, None, None, min(self.sample_fps, fps),
                                       self.encoder, self.detector, 'consumed_live_frames_jpeg')
        self.live_capture = _LatestCapture(capture, cv2, self.seconds)
        self._capture = self.live_capture
        return self


class LiveExperiment(ReplayExperiment):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.submitted_calls = self.submitted_jpeg_bytes = self.submitted_images = 0
        self.cpu_start = time.process_time()
        self.live_source = None

    def log(self, event):
        super().log(event)
        if event['type'] == 'request':
            self.submitted_calls += 1
            self.submitted_jpeg_bytes += event['image_bytes']
            self.submitted_images += len(event['frame_times'])
        if event['type'] in ('request', 'result', 'model_error', 'notification', 'watch'):
            print(json.dumps(event, allow_nan=False), flush=True)

    def report(self, status, metadata):
        summary = super().report(status, metadata)
        summary['buffer_kind'] = 'consumed_live_frames_jpeg'
        capture = self.live_source.live_capture if self.live_source else None
        observed = summary['media_seconds_observed']
        cpu = time.process_time() - self.cpu_start
        summary['live'] = {
            'capture': capture.metrics() if capture else None,
            'submitted_calls': self.submitted_calls, 'submitted_images': self.submitted_images,
            'submitted_jpeg_bytes': self.submitted_jpeg_bytes,
            'calls_per_observed_minute': self.submitted_calls * 60 / observed if observed else None,
            'input_tokens_per_observed_minute': self.usage['input_tokens'] * 60 / observed if observed else None,
            'output_tokens_per_observed_minute': self.usage['output_tokens'] * 60 / observed if observed else None,
            'process_cpu_seconds': cpu,
            'mean_cpu_cores_including_drain': cpu / summary['wall_seconds'] if summary['wall_seconds'] else None,
            'process_peak_rss_platform_units': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'rss_units': 'bytes on macOS; KiB on Linux',
        }
        summary['limits'] += [
            'Live decoder overwrites unconsumed frames; brief events can be missed.',
            'Frame timestamps are local decode arrivals, not camera sensor timestamps.',
            'JPEG byte totals exclude base64/JSON/HTTP overhead and RTSP network traffic.',
            'Reported token rates omit usage unavailable after failed requests.',
            'Local CPU/RSS measurements do not measure remote Qwen GPU or energy use.',
        ]
        return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--host', required=True, help='Private LAN IP from Tapo Device Info')
    parser.add_argument('--stream', choices=('stream1', 'stream2'), default='stream2')
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--backend', choices=('watch', 'alibaba'), default='watch')
    parser.add_argument('--ask-key', action='store_true')
    parser.add_argument('--sample-fps', type=float, default=2)
    parser.add_argument('--encoder', choices=('mobilenet', 'appearance'), default='mobilenet')
    parser.add_argument('--detector', choices=('ssdlite', 'none'), default='ssdlite')
    parser.add_argument('--model-cache', type=Path, default=Path('local/models'))
    parser.add_argument('--max-calls', type=int, default=8)
    parser.add_argument('--attend-interval', type=float, default=20)
    parser.add_argument('--inspect-interval', type=float, default=30)
    parser.add_argument('--goal', default='Describe meaningful visible changes and interactions. Inspect unexplained changes; notify only about concrete events supported by visible evidence. Ordinary activity is valid. Do not infer intent.')
    parser.add_argument('--output', type=Path)
    for stage in ('attend', 'inspect'):
        for direction in ('input', 'output'):
            parser.add_argument(f'--{stage}-{direction}-usd-per-million', type=float)
    args = parser.parse_args(argv)
    try:
        # Validate before asking for any secrets or opening a connection.
        camera_url(args.host, 'validation', 'validation', args.stream)
        if args.ask_key and args.backend != 'alibaba':
            raise ValueError('--ask-key requires --backend alibaba')
        config = AttentionConfig(attend_interval=args.attend_interval, inspect_interval=args.inspect_interval,
                                 max_calls=args.max_calls)
        prices = {}
        for stage in ('attend', 'inspect'):
            incoming, outgoing = (getattr(args, f'{stage}_{direction}_usd_per_million') for direction in ('input', 'output'))
            if (incoming is None) != (outgoing is None):
                raise ValueError(f'Provide both input and output prices for {stage}')
            prices[f'{stage}_prices'] = None if incoming is None else ModelPrices(incoming, outgoing)
        model_config = ModelConfig(**prices)
        output = args.output or Path('local/camera') / time.strftime('%Y%m%d-%H%M%S')
        if output.exists():
            raise ValueError('Choose a new output directory; existing runs are never overwritten')
        # Validate source settings without opening the camera.
        source = LiveVideoSource('unused', seconds=args.seconds, sample_fps=args.sample_fps,
                                 encoder=args.encoder, detector=args.detector, model_cache=args.model_cache)
    except ValueError as error:
        parser.error(str(error))
    username = os.environ.get('TAPO_CAMERA_USERNAME') or input('Tapo Camera Account username (not cloud login): ')
    password = os.environ.get('TAPO_CAMERA_PASSWORD') or getpass.getpass('Tapo Camera Account password (not saved): ')
    try:
        source._url = camera_url(args.host, username, password, args.stream)
    except ValueError as error:
        parser.error(str(error))
    key = None
    if args.backend == 'alibaba':
        print('Alibaba mode sends selected camera images to the Singapore Qwen endpoint; max calls:', args.max_calls)
        key = getpass.getpass('Alibaba API key (not saved): ') if args.ask_key else os.environ.get('ALIBABA_API_KEY', '')
        if not key.strip():
            parser.error('Use --ask-key or ALIBABA_API_KEY; never paste keys into chat')
    previous_key = os.environ.get('ALIBABA_API_KEY')
    if key:
        os.environ['ALIBABA_API_KEY'] = key
    client = AlibabaVisionClient(model_config) if key else None
    experiment = LiveExperiment(args.goal, output, client=client, config=config, realtime=True)
    experiment.live_source = source
    source.on_frame = experiment.buffer_frame
    try:
        with source:
            summary = experiment.run(source, metadata=asdict(source.metadata))
    except (RuntimeError, ValueError) as error:
        print(str(error))
        return 1
    finally:
        if previous_key is None:
            os.environ.pop('ALIBABA_API_KEY', None)
        else:
            os.environ['ALIBABA_API_KEY'] = previous_key
    print(json.dumps({'status': summary['status'], 'report': str((output / 'report.html').resolve()),
                      'usage': summary['usage'], 'live': summary['live']}, indent=2))
    return 0 if summary['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
