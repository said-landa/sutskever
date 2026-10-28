"""Camera bridge acceptance checks; fixture images/models, never paid inference."""
from concurrent.futures import wait
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from vision.camera_gateway import CameraGateway, GatewayExperiment, PushDelivery, make_server
from vision.stream_attention import AttentionConfig
from vision.stream_models import AnalysisResult, TokenUsage
from vision.stream_video import FrameObservation

JPEG = b'\xff\xd8fixture-image\xff\xd9'


class GatewayTests(unittest.TestCase):
    def test_http_auth_origin_limits_and_no_image_in_state(self):
        gateway = CameraGateway(client_factory=lambda model: None)
        gateway.publish(FrameObservation(time=0, frame_index=0, jpeg=JPEG))
        server = make_server(gateway, 'fixture-token', ('127.0.0.1', 0))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with self.assertRaises(HTTPError) as caught:
                urlopen(base + '/api/frame')
            self.assertEqual(caught.exception.code, 401)
            headers = {'Authorization': 'Bearer fixture-token'}
            with urlopen(Request(base + '/api/frame', headers=headers)) as response:
                self.assertEqual(response.read(), JPEG)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
            with urlopen(Request(base + '/api/state', headers=headers)) as response:
                data = response.read()
                self.assertNotIn(JPEG, data)
                self.assertNotIn(b'fixture-token', data)
                self.assertEqual(json.loads(data)['camera']['status'], 'streaming')
            for extra in ({}, {'X-Vision-Client': '1', 'Origin': 'http://other.example'}):
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(base + '/api/stop', data=b'{}', headers={**headers, **extra}))
                self.assertEqual(caught.exception.code, 403)
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(base + '/api/monitor', data=b'x' * 9000,
                                headers={**headers, 'X-Vision-Client': '1'}))
            self.assertEqual(caught.exception.code, 400)
            gateway.frame_at = time.time() - 10
            self.assertEqual(gateway.snapshot()['camera']['status'], 'stale')
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(base + '/api/frame', headers=headers))
            self.assertEqual(caught.exception.code, 503)
        finally:
            server.shutdown(); server.server_close(); thread.join(2)

    def test_goal_evidence_pending_usage_and_semantic_notifications(self):
        class Push(PushDelivery):
            sent = []
            def send(self, alert, completion):
                self.sent.append(alert)
                completion('accepted_by_apns')

        class Client:
            requests = []
            def analyze(self, request):
                self.requests.append(request)
                return AnalysisResult('inspect' if request.stage == 'attend' else 'notify',
                    'Fixture: someone crosses the doorway in the supplied images.',
                    'Fixture doorway crossing.', None, TokenUsage(100, 10), 0.1,
                    'fixture-model', request.stage)

        with tempfile.TemporaryDirectory() as folder:
            client, push = Client(), Push(None, None)
            gateway = CameraGateway(client_factory=lambda model: client, push=push)
            gateway.monitor.update(run_id='fixture-run', max_calls=4, model='fixture-model')
            experiment = GatewayExperiment(gateway, 'Notify when someone crosses the doorway.',
                Path(folder) / 'run', client=client,
                config=AttentionConfig(attend_interval=100, inspect_interval=100, max_calls=4))
            def frames():
                yield FrameObservation(0, 0, JPEG)
                yield FrameObservation(1, 1, JPEG)
                self.assertEqual(gateway.snapshot()['monitor']['pending'], [1])
                wait([job[1] for job in experiment.jobs.values()], timeout=2)
                yield FrameObservation(2, 2, JPEG)
            experiment.run(frames())
            state = gateway.snapshot()
            self.assertGreater(len(client.requests), 1)
            self.assertTrue(all(r.goal == 'Notify when someone crosses the doorway.' for r in client.requests))
            self.assertEqual(state['monitor']['input_tokens'], 100 * len(client.requests))
            self.assertEqual(state['monitor']['pending'], [])
            self.assertGreater(len(state['alerts']), 0)
            self.assertEqual(state['alerts'][0]['delivery'], 'accepted_by_apns')
            self.assertEqual(len(push.sent), len(state['alerts']))
            for request_id, frames in gateway.evidence.items():
                request_event = next(e for e in experiment.events if e.get('type') == 'request' and str(e['id']) == request_id)
                self.assertEqual([f.time for f in frames], request_event['frame_times'])
                self.assertTrue(all(f.jpeg == JPEG for f in frames))

    def test_stop_suppresses_new_requests_and_alerts_from_old_goal(self):
        class Client:
            calls = 0
            def analyze(self, request):
                self.calls += 1
                return AnalysisResult('notify', 'Fixture evidence.', 'Fixture event.', None,
                                      TokenUsage(1, 1), 0.1, 'fixture', request.stage)
        with tempfile.TemporaryDirectory() as folder:
            gateway = CameraGateway(client_factory=lambda model: None)
            client = Client()
            experiment = GatewayExperiment(gateway, 'Watch door.', Path(folder) / 'run', client=client)
            gateway.stop()
            experiment.run([FrameObservation(0, 0, JPEG), FrameObservation(1, 1, JPEG)])
            self.assertEqual(client.calls, 0)
            self.assertEqual(gateway.snapshot()['alerts'], [])

    def test_registration_validation_and_push_not_configured_is_honest(self):
        push = PushDelivery(None, None)
        for environment in ('sandbox', 'production'):
            push.register({'deviceToken': 'ab' * 32, 'topic': 'com.vision.lab', 'environment': environment})
        statuses = []
        push.send({'summary': 'Fixture'}, statuses.append)
        self.assertEqual(statuses, ['not_configured'])
        for invalid in ({'deviceToken': 'secret\n', 'topic': 'com.vision.lab', 'environment': 'production'},
                        {'deviceToken': 'ab' * 32, 'topic': 'com.vision.live', 'environment': 'production'}):
            with self.assertRaises(ValueError):
                push.register(invalid)

    def test_pending_old_goal_cannot_send_alert_after_stop(self):
        class Client:
            def analyze(self, request):
                return AnalysisResult('notify', 'Fixture evidence.', 'Fixture event.', None,
                                      TokenUsage(1, 1), 0.1, 'fixture', request.stage)
        with tempfile.TemporaryDirectory() as folder:
            gateway = CameraGateway(client_factory=lambda model: None)
            gateway.monitor.update(run_id='old-goal', max_calls=3)
            experiment = GatewayExperiment(gateway, 'Old goal.', Path(folder) / 'run', client=Client(),
                                           config=AttentionConfig(inspect_interval=1))
            def frames():
                yield FrameObservation(0, 0, JPEG)
                yield FrameObservation(1, 1, JPEG)
                wait([job[1] for job in experiment.jobs.values()], timeout=2)
                gateway.stop()
                yield FrameObservation(2, 2, JPEG)
            experiment.run(frames())
            self.assertEqual(gateway.snapshot()['alerts'], [])

    def test_existing_config_with_missing_key_is_not_reported_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'config.json'
            config.write_text(json.dumps({'keyID': 'fixture', 'teamID': 'fixture',
                                          'keyFile': str(Path(folder) / 'missing.p8')}))
            push = PushDelivery(Path(__file__), config)
            self.assertFalse(push.configured)

    def test_start_requires_fresh_camera_and_does_not_accept_unknown_models(self):
        gateway = CameraGateway(client_factory=lambda model: None)
        with self.assertRaises(ValueError):
            gateway.start('Watch door.', 'qwen3-vl-plus', 10)
        gateway.publish(FrameObservation(0, 0, JPEG))
        with self.assertRaises(ValueError):
            gateway.start('Watch door.', 'arbitrary-model', 10)
        with self.assertRaises(ValueError):
            gateway.start('Watch door.', 'qwen3-vl-plus', True)


if __name__ == '__main__':
    unittest.main()
