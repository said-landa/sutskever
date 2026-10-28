"""Authenticated LAN camera bridge for Vision's native Live tab.

Camera/API credentials stay on the Mac. Run with --host CAMERA_IP --ask-key.
Pair the phone using the token in local/gateway/pairing-token (never commit it).
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict, replace
import getpass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time
import uuid

from vision.stream_attention import AttentionConfig
from vision.stream_live import LiveVideoSource, camera_url
from vision.stream_models import AlibabaVisionClient, ModelConfig
from vision.stream_replay import ReplayExperiment


class PushDelivery:
    """Bounded off-thread APNs delivery; no signing credentials reach the phone."""
    def __init__(self, binary: Path | None, config: Path | None):
        self.binary, self.config = binary, config
        self.registration = None
        self.lock = threading.Lock()
        self.busy = threading.BoundedSemaphore(4)

    @property
    def configured(self):
        if not (self.binary and self.config and self.binary.is_file() and self.config.is_file()):
            return False
        try:
            if self.config.stat().st_size > 8192:
                return False
            settings = json.loads(self.config.read_text())
            return bool(settings.get('keyID') and settings.get('teamID') and Path(settings['keyFile']).is_file())
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return False

    def register(self, registration):
        token = registration.get('deviceToken', '')
        if (not isinstance(token, str) or not 32 <= len(token) <= 512 or len(token) % 2
                or any(c not in '0123456789abcdefABCDEF' for c in token)
                or registration.get('topic') != 'com.vision.lab'
                or registration.get('environment') not in ('sandbox', 'production')):
            raise ValueError('Invalid Vision notification registration')
        with self.lock:
            self.registration = dict(registration)

    def send(self, alert, completion):
        with self.lock:
            registration = self.registration
        if not self.configured or not registration:
            completion('not_configured')
            return
        if not self.busy.acquire(blocking=False):
            completion('queue_full')
            return

        def deliver():
            try:
                result = subprocess.run([str(self.binary), '--event-stdin', '--config', str(self.config)],
                    input=json.dumps({'registration': registration, 'title': 'Vision · Detection',
                                      'body': alert['summary'][:500]}).encode(),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
                completion('accepted_by_apns' if result.returncode == 0 else 'failed')
            except (OSError, subprocess.TimeoutExpired):
                completion('failed')
            finally:
                self.busy.release()
        threading.Thread(target=deliver, daemon=True, name='vision-push').start()


class CameraGateway:
    def __init__(self, *, client_factory, output=Path('local/gateway/runs'), push=None):
        self.client_factory, self.output = client_factory, Path(output)
        self.push = push or PushDelivery(None, None)
        self.condition = threading.Condition(threading.RLock())
        self.shutdown = threading.Event()
        self.stop_monitor = threading.Event()
        self.worker = None
        self.sequence = 0
        self.frame = None
        self.frame_at = None
        self.camera_status = 'connecting'
        self.monitor = {'phase': 'idle', 'goal': '', 'run_id': '', 'model': '', 'pending': [],
                        'calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'max_calls': 0}
        self.results = deque(maxlen=20)
        self.alerts = deque(maxlen=30)
        self.evidence = {}  # at most 3 requests, each already limited by the adapter
        self.error = None

    def publish(self, frame):
        with self.condition:
            self.frame, self.frame_at = frame, time.time()
            self.sequence += 1
            self.camera_status = 'streaming'
            self.condition.notify_all()

    def capture(self, source):
        try:
            with source:
                for frame in source:
                    if self.shutdown.is_set():
                        break
                    self.publish(frame)
            with self.condition:
                self.camera_status = 'stopped'
                self.condition.notify_all()
        except Exception:
            with self.condition:
                self.camera_status = 'error'
                self.error = 'Camera disconnected or could not open. Check camera account, LAN and power.'
                self.condition.notify_all()

    def frames(self):
        previous, base = self.sequence, None
        while not self.shutdown.is_set() and not self.stop_monitor.is_set():
            with self.condition:
                self.condition.wait_for(lambda: self.sequence != previous or self.shutdown.is_set()
                    or self.stop_monitor.is_set() or self.camera_status in ('error', 'stopped'), timeout=2)
                if self.shutdown.is_set() or self.stop_monitor.is_set():
                    return
                if self.camera_status in ('error', 'stopped'):
                    raise RuntimeError('Camera stream is no longer available')
                if self.sequence == previous:
                    if self.frame_at and time.time() - self.frame_at > 6:
                        raise RuntimeError('Camera frames are stale')
                    continue
                previous, frame = self.sequence, self.frame
            base = frame.time if base is None else base
            yield replace(frame, time=frame.time - base)

    def start(self, goal, model, max_calls):
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 2000:
            raise ValueError('Enter a monitoring goal of 1–2000 characters')
        if model not in ('qwen3-vl-plus', 'qwen3-vl-flash', 'qwen3-vl-8b-instruct', 'qwen3-vl-32b-instruct'):
            raise ValueError('Choose a supported vision model')
        if type(max_calls) is not int or not 1 <= max_calls <= 300:
            raise ValueError('Request limit must be 1–300')
        with self.condition:
            if self.worker and self.worker.is_alive():
                raise ValueError('Stop the current monitoring session and wait for pending analysis')
            if self.camera_status != 'streaming' or not self.frame_at or time.time() - self.frame_at > 3:
                raise ValueError('Connect a live camera before starting monitoring')
            client = self.client_factory(model)
            run_id = uuid.uuid4().hex
            self.stop_monitor.clear()
            self.results.clear()
            self.alerts.clear()
            self.evidence.clear()
            self.error = None
            self.monitor = dict(phase='watching', goal=goal.strip(), run_id=run_id, model=model,
                                pending=[], calls=0, input_tokens=0, output_tokens=0, max_calls=max_calls)

            def run():
                experiment = GatewayExperiment(self, goal.strip(), self.output / run_id, client=client,
                    config=AttentionConfig(max_calls=max_calls, attend_interval=10, inspect_interval=30))
                try:
                    summary = experiment.run(self.frames())
                    with self.condition:
                        self.monitor['phase'] = ('stopped' if self.stop_monitor.is_set() else summary['status'])
                except Exception:
                    with self.condition:
                        self.monitor['phase'] = 'error'
                        if not self.error:
                            self.error = 'Monitoring stopped. Check the camera and model connection.'
                finally:
                    with self.condition:
                        self.monitor['pending'] = []
            self.worker = threading.Thread(target=run, daemon=True, name='vision-monitor')
            self.worker.start()

    def stop(self):
        with self.condition:
            self.stop_monitor.set()
            if self.worker and self.worker.is_alive():
                self.monitor['phase'] = 'stopping'
            self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            age = time.time() - self.frame_at if self.frame_at else None
            camera = 'stale' if self.camera_status == 'streaming' and age is not None and age > 3 else self.camera_status
            return dict(camera=dict(status=camera, frame_age_seconds=age, sequence=self.sequence),
                        monitor=dict(self.monitor), results=list(self.results), alerts=list(self.alerts),
                        evidence=[{'request_id': key, 'times': [f.time for f in frames]}
                                  for key, frames in self.evidence.items()], error=self.error,
                        push_configured=self.push.configured, push_registered=self.push.registration is not None)


class GatewayExperiment(ReplayExperiment):
    def __init__(self, gateway, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.gateway = gateway
        self.halted = False

    def submit(self, plan):
        if self.gateway.stop_monitor.is_set() or self.halted:
            self.controller.fail(plan, 'Monitoring stopped; no new model request was sent', self.last_observed)
            return
        super().submit(plan)

    def log(self, event):
        super().log(event)
        gateway = self.gateway
        with gateway.condition:
            kind = event['type']
            if kind == 'request':
                key = str(event['id'])
                # Exact images/timestamps from the submitted request, kept only
                # in bounded RAM for the phone's "Sent to Qwen" strip.
                selected = {f.time: f for f in [*self.anchors, *self.ring.select(0, event['observed_until'], 7200)]}
                gateway.evidence[key] = [selected[t] for t in event['frame_times'] if t in selected]
                while len(gateway.evidence) > 3:
                    gateway.evidence.pop(next(iter(gateway.evidence)))
                gateway.monitor['pending'].append(event['id'])
                gateway.monitor['calls'] += 1
            elif kind in ('result', 'model_error'):
                gateway.monitor['pending'] = [p for p in gateway.monitor['pending'] if p != event['id']]
                gateway.results.appendleft(dict(event, received_at=time.time()))
                if kind == 'result':
                    usage = event.get('usage') or {}
                    gateway.monitor['input_tokens'] += usage.get('input_tokens', 0)
                    gateway.monitor['output_tokens'] += usage.get('output_tokens', 0)
                else:
                    gateway.error = event['message']
            elif kind == 'notification' and not gateway.stop_monitor.is_set() and not self.halted:
                alert = dict(id=gateway.monitor['run_id'] + '-' + event['id'], summary=event['summary'],
                             evidence=event['evidence'], received_at=time.time(), delivery='pending')
                gateway.alerts.appendleft(alert)
                def delivered(status):
                    with gateway.condition:
                        alert['delivery'] = status
                gateway.push.send(alert, delivered)
            if kind == 'watch' and gateway.monitor['calls'] >= gateway.monitor['max_calls'] and not self.jobs:
                gateway.monitor['phase'] = 'budget_exhausted'
            elif gateway.monitor['phase'] not in ('stopping', 'error', 'budget_exhausted'):
                gateway.monitor['phase'] = 'analyzing' if gateway.monitor['pending'] else 'watching'

    def poll(self):
        try:
            super().poll()
        except Exception:
            self.halted = True
            raise


def make_server(gateway, token, address):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *args):
            pass  # Never log headers, pairing secrets, registration or imagery.

        def reply(self, status, body, content_type='application/json'):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            supplied = self.headers.get('Authorization', '')
            if not secrets.compare_digest(supplied.encode(), ('Bearer ' + token).encode()):
                self.reply(401, b'{"error":"Pairing required"}')
                return False
            return True

        def do_GET(self):
            if not self.authorized():
                return
            if self.path == '/api/state':
                self.reply(200, json.dumps(gateway.snapshot(), allow_nan=False).encode())
            elif self.path == '/api/frame':
                with gateway.condition:
                    frame = gateway.frame
                    fresh = gateway.frame_at and time.time() - gateway.frame_at <= 3
                self.reply(200, frame.jpeg, 'image/jpeg') if frame and fresh else self.reply(503, b'{"error":"No fresh frame"}')
            elif self.path.startswith('/api/evidence/'):
                parts = self.path.split('/')
                with gateway.condition:
                    frames = gateway.evidence.get(parts[3], []) if len(parts) == 5 else []
                    index = int(parts[4]) if len(parts) == 5 and parts[4].isdigit() else -1
                    frame = frames[index] if 0 <= index < len(frames) else None
                self.reply(200, frame.jpeg, 'image/jpeg') if frame else self.reply(404, b'{}')
            else:
                self.reply(404, b'{}')

        def do_POST(self):
            if not self.authorized():
                return
            if self.headers.get('Origin') or self.headers.get('X-Vision-Client') != '1':
                self.reply(403, b'{"error":"Use the paired Vision app"}')
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 8192:
                    raise ValueError('Invalid request size')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('Expected an object')
                if self.path == '/api/monitor':
                    gateway.start(body.get('goal'), body.get('model'), body.get('max_calls'))
                elif self.path == '/api/stop':
                    gateway.stop()
                elif self.path == '/api/registration':
                    gateway.push.register(body)
                else:
                    self.reply(404, b'{}')
                    return
                self.reply(200, b'{"ok":true}')
            except (ValueError, TypeError, UnicodeError):
                self.reply(400, b'{"error":"Invalid settings or current session still active"}')

    class BoundedServer(ThreadingHTTPServer):
        slots = threading.BoundedSemaphore(8)
        request_queue_size = 8
        def process_request(self, request, client_address):
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, client_address)
            except Exception:
                self.slots.release()
                raise
        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self.slots.release()
    server = BoundedServer(address, Handler)
    server.daemon_threads = True
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--bind', default='127.0.0.1', help='Use your Mac LAN IP to allow the paired iPhone')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--seconds', type=float, default=3600)
    parser.add_argument('--ask-key', action='store_true')
    parser.add_argument('--endpoint', default=ModelConfig().endpoint)
    parser.add_argument('--push-binary', type=Path, default=Path('local/gateway/send_push'))
    parser.add_argument('--push-config', type=Path, default=Path('local/push-config.json'))
    args = parser.parse_args(argv)
    ModelConfig(endpoint=args.endpoint)
    camera_url(args.host, 'validation', 'validation', 'stream2')
    username = input('Tapo Camera Account username: ')
    password = getpass.getpass('Tapo camera password (not saved): ')
    key = getpass.getpass('Alibaba API key (not saved): ') if args.ask_key else os.environ.get('ALIBABA_API_KEY', '')
    if key.strip():
        os.environ['ALIBABA_API_KEY'] = key.strip()
    url = camera_url(args.host, username, password, 'stream2')
    def client_factory(model):
        if not os.environ.get('ALIBABA_API_KEY', '').strip():
            raise ValueError('Restart the gateway with --ask-key to enable Qwen analysis')
        return AlibabaVisionClient(ModelConfig(endpoint=args.endpoint, attend_model=model, inspect_model=model))
    gateway = CameraGateway(client_factory=client_factory, push=PushDelivery(args.push_binary, args.push_config))
    folder = Path('local/gateway')
    folder.mkdir(parents=True, exist_ok=True)
    token_path = folder / 'pairing-token'
    # Fresh token per launch; the file is private and ignored by Git.
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    token = secrets.token_urlsafe(32)
    with os.fdopen(fd, 'w') as stream:
        stream.write(token)
    server = make_server(gateway, token, (args.bind, args.port))
    source = LiveVideoSource(url, seconds=args.seconds, sample_fps=5, encoder='appearance', detector='none')
    threading.Thread(target=gateway.capture, args=(source,), daemon=True, name='vision-camera').start()
    print('Vision camera bridge listening on port', server.server_port, flush=True)
    print('Pair in Vision Live using this Mac address and local/gateway/pairing-token. No browser is required.', flush=True)
    print('Keep this Mac awake on the same trusted Wi-Fi. LAN HTTP is not encrypted; do not expose this port publicly.', flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        gateway.stop()
        gateway.shutdown.set()
        with gateway.condition:
            gateway.condition.notify_all()
        server.server_close()


if __name__ == '__main__':
    main()
