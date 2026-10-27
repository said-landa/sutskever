"""Integration checks with fixture model replies, never paid inference.

The blocked client verifies that video ingestion continues while a model call is
pending. Synthetic replies establish pipeline behavior, not event recognition.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
import unittest

from vision.stream_attention import AttentionConfig
from vision.stream_models import AnalysisResult, ModelHTTPError, ModelResponseError, TokenUsage
from vision.stream_replay import ReplayExperiment
from vision.stream_video import FrameObservation


JPEG = b"\xff\xd8UNIQUE_TEST_IMAGE_BYTES\xff\xd9"


def frame(second: float) -> FrameObservation:
    return FrameObservation(time=second, frame_index=round(second * 10), jpeg=JPEG)


def response(request, action: str = "observe", *, usage=None, latency: float = 0.0):
    return AnalysisResult(
        action=action,
        evidence="Fixture: visible evidence through the submitted cutoff only.",
        event_summary="A fixture event." if action == "notify" else "Fixture scene interpretation.",
        recheck_after_seconds=None,
        usage=usage,
        latency_seconds=latency,
        model="fixture-model",
        stage=request.stage,
    )


class ReplayAcceptanceTests(unittest.TestCase):
    def assert_final_tail_is_reviewed_once(self, end: float):
        """An ordinary early reply must not leave later captured images unseen."""
        class Client:
            def __init__(self):
                self.started = threading.Event()
                self.requests = []
                self.experiment = None

            def analyze(self, request):
                self.requests.append(request)
                if len(self.requests) > 3:
                    raise AssertionError("EOF scheduling must terminate after the final escalation")
                if len(self.requests) == 1:
                    self.started.set()
                    deadline = time.monotonic() + 3
                    while not self.experiment.eof:
                        if time.monotonic() >= deadline:
                            raise AssertionError("The early request blocked input ingestion")
                        time.sleep(0.001)
                    return response(request, "observe", latency=0.1)
                return response(request, "inspect" if request.stage == "attend" else "notify",
                                latency=0.1)

        with tempfile.TemporaryDirectory() as directory:
            client = Client()
            experiment = ReplayExperiment(
                "Inspect relevant changes.", Path(directory) / "tail", client=client,
                config=AttentionConfig(attend_interval=100.0, inspect_interval=100.0,
                                       minimum_call_interval=3.0, max_calls=6),
            )
            client.experiment = experiment

            def source():
                yield frame(0.0)
                yield frame(1.0)
                self.assertTrue(client.started.wait(2), "The early model worker did not start")
                yield frame(end)

            summary = experiment.run(source())
            self.assertEqual([request.stage for request in client.requests],
                             ["attend", "attend", "inspect"])
            self.assertEqual([request.observed_until_seconds for request in client.requests],
                             [1.0, end, end])
            final_requests = [event for event in experiment.events
                              if event["type"] == "request" and event["after_input_ended"]]
            self.assertEqual(len(final_requests), 2)
            self.assertIn("final_unreviewed_context", final_requests[0]["reasons"])
            self.assertIn("semantic_escalation_after_input", final_requests[1]["reasons"])
            for request in client.requests:
                self.assertTrue(all(sample.timestamp_seconds <= request.observed_until_seconds <= end
                                    for sample in request.frames))
            self.assertEqual(client.requests[1].frames[-1].timestamp_seconds, end)
            self.assertEqual(client.requests[2].frames[-1].timestamp_seconds, end)
            self.assertEqual(summary["controller"]["calls"], {"attend": 2, "inspect": 1})
            self.assertEqual(summary["controller"]["pending"], {})
            self.assertFalse(summary["controller"]["deferred_inspection"])
            self.assertEqual(summary["controller"]["denied"], 0)
            notifications = [event for event in experiment.events if event["type"] == "notification"]
            self.assertEqual(len(notifications), 1)
            self.assertTrue(notifications[0]["after_input_ended"])
            self.assertEqual(notifications[0]["observed_until"], end)
            self.assertGreater(notifications[0]["available_at"], end)

    def test_pending_old_ordinary_reply_gets_one_final_tail_review_and_escalation(self):
        self.assert_final_tail_is_reviewed_once(5.0)

    def test_final_tail_under_call_cooldown_is_still_reviewed_once(self):
        self.assert_final_tail_is_reviewed_once(1.1)

    def test_watch_only_retains_unknown_semantics_and_never_claims_model_savings(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "watch"
            experiment = ReplayExperiment("Watch for relevant events.", output)
            summary = experiment.run([frame(float(second)) for second in range(12)])
            self.assertEqual(summary["mode"], "watch_only")
            self.assertEqual(summary["usage"]["responses_with_usage"], 0)
            self.assertEqual(summary["controller"]["notifications"], [])
            self.assertTrue(summary["controller"]["unresolved"])
            self.assertEqual(summary["controller"]["health"], "degraded")
            self.assertTrue(any(event["type"] == "planned_request" for event in experiment.events))
            self.assertFalse(any(event["type"] == "result" for event in experiment.events))
            self.assertIn("No recall, false-alarm rate, or savings claim", " ".join(summary["limits"]))
            self.assertIsNone(summary.get("estimated_cost_usd"))
            self.assertFalse((output / "notifications.jsonl").exists())

    def test_pending_capture_eof_escalation_pinned_evidence_and_sanitized_outbox(self):
        class Client:
            def __init__(self):
                self.started = threading.Event()
                self.requests = []
                self.experiment = None

            def analyze(self, request):
                self.requests.append(request)
                if request.stage == "attend":
                    self.started.set()
                    deadline = time.monotonic() + 3
                    while not self.experiment.eof:
                        if time.monotonic() >= deadline:
                            raise AssertionError("Input stopped while fixture model was pending")
                        time.sleep(0.001)
                    return response(request, "inspect", usage=TokenUsage(100, 10), latency=25.0)
                return response(request, "notify", usage=TokenUsage(200, 20), latency=2.0)

        with tempfile.TemporaryDirectory() as directory:
            client = Client()
            experiment = ReplayExperiment(
                "Inspect visible changes.", Path(directory) / "replay", client=client,
                config=AttentionConfig(inspect_interval=100.0, attend_interval=100.0),
                ring_seconds=2.0,
            )
            client.experiment = experiment

            def source():
                yield frame(0.0)
                yield frame(1.0)
                self.assertTrue(client.started.wait(2), "Fixture model worker did not start")
                for second in range(2, 21):
                    self.assertEqual(len(client.requests), 1)
                    yield frame(float(second))

            summary = experiment.run(source())
            self.assertEqual(summary["sampled_observations"], 21)
            self.assertGreater(summary["ring_evictions"], 0)
            self.assertEqual([request.stage for request in client.requests], ["attend", "inspect"])
            first, deep = client.requests
            self.assertEqual([sample.timestamp_seconds for sample in first.frames], [0.0, 1.0])
            self.assertEqual(deep.observed_until_seconds, 20.0)
            self.assertIn(0.0, [sample.timestamp_seconds for sample in deep.frames])
            self.assertEqual(deep.frames[-1].timestamp_seconds, 20.0)
            for request in client.requests:
                self.assertLessEqual(len(request.frames), experiment.max_frames)
                self.assertTrue(all(sample.timestamp_seconds <= request.observed_until_seconds
                                    for sample in request.frames))
            self.assertEqual(summary["usage"]["input_tokens"], 300)
            self.assertEqual(summary["usage"]["output_tokens"], 30)
            self.assertEqual(summary["usage"]["responses_with_usage"], 2)
            notifications = [event for event in experiment.events if event["type"] == "notification"]
            self.assertEqual(len(notifications), 1)
            self.assertEqual(notifications[0]["delivery"], "local_outbox")
            self.assertTrue(notifications[0]["after_input_ended"])
            self.assertGreaterEqual(notifications[0]["available_at"], 20.0)
            first_result = next(event for event in experiment.events if event["type"] == "result")
            self.assertGreaterEqual(notifications[0]["available_at"], first_result["available_at"] + 2.0)
            saved = "\n".join(path.read_text() for path in experiment.output.iterdir())
            self.assertNotIn("UNIQUE_TEST_IMAGE_BYTES", saved)
            self.assertNotIn("data:image", saved)
            self.assertNotIn("Authorization", saved)
            for line in (experiment.output / "events.jsonl").read_text().splitlines():
                json.loads(line)
            json.loads((experiment.output / "summary.json").read_text())

    def test_every_source_frame_can_be_buffered_between_semantic_samples(self):
        class Client:
            def __init__(self):
                self.requests = []

            def analyze(self, request):
                self.requests.append(request)
                return response(request)

        with tempfile.TemporaryDirectory() as directory:
            client = Client()
            experiment = ReplayExperiment(
                "Inspect visible changes.", Path(directory) / "all-frames", client=client,
                config=AttentionConfig(attend_interval=100.0, inspect_interval=100.0),
                max_frames=24,
            )

            def source():
                for index in range(21):
                    observation = frame(index / 10)
                    experiment.buffer_frame(observation)
                    if index % 10 == 0:
                        yield observation

            summary = experiment.run(source())
            self.assertEqual(summary["sampled_observations"], 3)
            self.assertEqual(summary["buffered_source_frames"], 21)
            self.assertEqual(summary["buffer_kind"], "all_frames_jpeg")
            self.assertTrue(any(0 < sample.timestamp_seconds < 1
                                for sample in client.requests[0].frames))
            self.assertLessEqual(client.requests[0].frames[-1].timestamp_seconds, 1.0)

    def test_cost_is_known_only_when_the_reply_has_an_explicit_estimate(self):
        class Client:
            def __init__(self, estimate):
                self.estimate = estimate

            def analyze(self, request):
                return replace(response(request, usage=TokenUsage(100, 10)),
                               estimated_cost_usd=self.estimate)

        with tempfile.TemporaryDirectory() as directory:
            for label, estimate in (("known", 0.0003), ("unknown", None)):
                with self.subTest(estimate=estimate):
                    experiment = ReplayExperiment(
                        "Inspect visible changes.", Path(directory) / label,
                        client=Client(estimate),
                    )
                    summary = experiment.run([frame(0.0), frame(1.0)])
                    self.assertEqual(summary["estimated_cost_usd"], estimate)
                    self.assertEqual(summary["responses_without_cost_estimate"], int(estimate is None))

    def test_unexpected_model_exception_is_sanitized_and_remains_unknown(self):
        secret = "SENTINEL_NEVER_PERSIST_PROVIDER_SECRET"

        class Client:
            def analyze(self, request):
                raise RuntimeError(secret)

        with tempfile.TemporaryDirectory() as directory:
            experiment = ReplayExperiment(
                "Watch for relevant events.", Path(directory) / "failure", client=Client(),
                config=AttentionConfig(attend_interval=100.0, inspect_interval=100.0),
            )
            summary = experiment.run([frame(0.0), frame(1.0)])
            self.assertEqual(summary["controller"]["health"], "degraded")
            self.assertTrue(summary["controller"]["unresolved"])
            self.assertEqual(summary["controller"]["notifications"], [])
            self.assertTrue(any(event["type"] == "model_error" for event in experiment.events))
            saved = "\n".join(path.read_text() for path in experiment.output.iterdir())
            self.assertNotIn(secret, saved)

    def test_invalid_semantic_reply_still_counts_provider_reported_usage(self):
        class Client:
            def analyze(self, request):
                raise ModelResponseError(
                    "Model returned an invalid decision.", usage=TokenUsage(123, 45),
                    latency_seconds=0.2, model="fixture-model", stage=request.stage,
                )

        with tempfile.TemporaryDirectory() as directory:
            experiment = ReplayExperiment(
                "Watch for relevant events.", Path(directory) / "invalid", client=Client(),
                config=AttentionConfig(attend_interval=100.0, inspect_interval=100.0),
            )
            summary = experiment.run([frame(0.0), frame(1.0)])
            self.assertEqual(summary["usage"]["input_tokens"], 123)
            self.assertEqual(summary["usage"]["output_tokens"], 45)
            self.assertTrue(summary["controller"]["unresolved"])
            self.assertEqual(summary["controller"]["notifications"], [])

    def test_output_directory_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing"
            output.mkdir()
            (output / "summary.json").write_text("prior-result")
            experiment = ReplayExperiment("Watch for relevant events.", output)
            try:
                with self.assertRaises(FileExistsError):
                    experiment.run([frame(0.0), frame(1.0)])
                self.assertEqual((output / "summary.json").read_text(), "prior-result")
            finally:
                experiment.executor.shutdown(wait=True, cancel_futures=True)

    def test_configuration_failure_stops_before_another_request_and_saves_failed_report(self):
        from concurrent.futures import wait

        class Client:
            calls = 0

            def analyze(self, request):
                self.calls += 1
                error = ModelHTTPError('Model unavailable; check model access and region.',
                                       status_code=404, provider_code='model_not_found')
                error.model, error.stage = 'fixture-model', request.stage
                raise error

        class Source:
            closed = False

            def __iter__(self):
                yield frame(0.0)
                yield frame(1.0)
                wait([job[1] for job in experiment.jobs.values()], timeout=2)
                yield frame(2.0)
                raise AssertionError('A permanent HTTP error must stop observation')

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as directory:
            client, source = Client(), Source()
            experiment = ReplayExperiment('Inspect visible changes.', Path(directory) / 'error', client=client)
            with self.assertRaises(ModelHTTPError):
                experiment.run(source)
            self.assertTrue(source.closed)
            self.assertEqual(client.calls, 1)
            summary = json.loads((experiment.output / 'summary.json').read_text())
            self.assertEqual(summary['status'], 'failed')
            self.assertEqual(summary['usage']['input_tokens'], 0)
            self.assertIsNone(summary['estimated_cost_usd'])
            event = next(e for e in experiment.events if e['type'] == 'model_error')
            self.assertEqual(event['http_status'], 404)
            self.assertEqual(event['provider_code'], 'model_not_found')
            self.assertEqual(event['model'], 'fixture-model')

    def test_fatal_reply_still_accounts_for_other_already_submitted_stage(self):
        from concurrent.futures import wait

        class Client:
            calls = 0

            def analyze(self, request):
                self.calls += 1
                if request.stage == 'inspect':
                    raise ModelHTTPError('Fixture model unavailable.', status_code=404)
                return response(request, usage=TokenUsage(123, 45))

        with tempfile.TemporaryDirectory() as directory:
            client = Client()
            experiment = ReplayExperiment('Inspect visible changes.', Path(directory) / 'parallel-error',
                client=client, config=AttentionConfig(inspect_interval=1))

            def source():
                yield frame(0)
                yield frame(1)
                wait([job[1] for job in experiment.jobs.values()], timeout=2)
                yield frame(2)

            with self.assertRaises(ModelHTTPError):
                experiment.run(source())
            self.assertEqual(client.calls, 2)
            summary = json.loads((experiment.output / 'summary.json').read_text())
            self.assertEqual(summary['usage']['input_tokens'], 123)
            self.assertEqual(summary['usage']['output_tokens'], 45)
            self.assertIsNone(summary['estimated_cost_usd'])
            self.assertEqual(experiment.jobs, {})


if __name__ == "__main__":
    unittest.main()
