"""Contract tests with synthetic model responses; no inference or network access."""

from __future__ import annotations

import base64
from dataclasses import replace
import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from vision.stream_models import (
    AlibabaVisionClient,
    FrameSample,
    ModelConfig,
    ModelPrices,
    ModelRequest,
    ModelResponseError,
    ModelHTTPError,
    ModelTransportError,
    SceneMemory,
    TokenUsage,
    _NoRedirect,
    _post,
)


JPEG = b"\xff\xd8synthetic-jpeg-fixture\xff\xd9"
SECRET = "synthetic-api-key-not-real"


def decision(action: str = "observe", delay: object = None) -> dict:
    return {
        "action": action,
        "evidence": "At 1.0s and 2.0s the scene is unchanged.",
        "event_summary": "No monitored event is established in the sampled images.",
        "recheck_after_seconds": delay,
    }


def completion(content: object = None, *, usage: object = True, finish: str = "stop") -> bytes:
    if content is None:
        content = decision()
    if not isinstance(content, str):
        content = json.dumps(content)
    result = {"choices": [{"finish_reason": finish, "message": {"content": content}}]}
    if usage is True:
        result["usage"] = {"prompt_tokens": 8500, "completion_tokens": 500, "total_tokens": 9000}
    elif usage is not False:
        result["usage"] = usage
    return json.dumps(result).encode()


def request(stage: str = "attend") -> ModelRequest:
    return ModelRequest(
        stage=stage,
        goal="Describe relevant activity, including a falling box if one is visible.",
        frames=(FrameSample(1.0, JPEG), FrameSample(2.0, JPEG)),
        observed_until_seconds=2.0,
        memory=SceneMemory(0.5, "A box was previously visible on the desk."),
    )


class ModelAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = patch.dict(os.environ, {"ALIBABA_API_KEY": SECRET})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def client(self, reply: bytes, config: ModelConfig | None = None):
        calls = []

        def transport(http_request, timeout, limit):
            calls.append((http_request, timeout, limit))
            return reply

        clock = iter((10.0, 10.25))
        client = AlibabaVisionClient(config, transport=transport, monotonic=lambda: next(clock))
        return client, calls

    def test_payload_keeps_chronology_context_and_jpeg_data(self) -> None:
        client, calls = self.client(completion())
        result = client.analyze(request())
        self.assertEqual(result.action, "observe")
        self.assertEqual(result.latency_seconds, 0.25)
        self.assertEqual(result.model, "qwen3-vl-flash")
        self.assertEqual(result.usage, TokenUsage(8500, 500))
        self.assertEqual(result.usage.total_tokens, 9000)
        self.assertIsNone(result.estimated_cost_usd)
        self.assertEqual(len(calls), 1)
        http_request, timeout, limit = calls[0]
        self.assertEqual(timeout, 60.0)
        self.assertEqual(limit, 262144)
        self.assertEqual(http_request.get_header("Authorization"), f"Bearer {SECRET}")
        self.assertEqual(http_request.get_method(), "POST")
        self.assertEqual(http_request.full_url, ModelConfig().endpoint)
        payload = json.loads(http_request.data)
        self.assertNotIn(SECRET, http_request.data.decode())
        self.assertEqual(payload["model"], "qwen3-vl-flash")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["max_tokens"], 600)
        self.assertFalse(payload["enable_thinking"])
        content = payload["messages"][1]["content"]
        context = json.loads(content[0]["text"])
        self.assertEqual(context["observation_cutoff_seconds"], 2.0)
        self.assertEqual(context["scene_memory"]["through_seconds"], 0.5)
        self.assertEqual(context["monitoring_goal"], request().goal)
        self.assertEqual(context["image_timestamps_seconds"], [1.0, 2.0])
        self.assertIn("1.000000", content[1]["text"])
        self.assertIn("2.000000", content[3]["text"])
        encoded = content[2]["image_url"]["url"].split(",", 1)[1]
        self.assertEqual(base64.b64decode(encoded), JPEG)

    def test_deep_notify_uses_configured_model_pricing_and_disabled_thinking(self) -> None:
        config = ModelConfig(
            timeout=12, max_output_tokens=321,
            inspect_prices=ModelPrices(0.20, 1.60),
        )
        client, calls = self.client(completion(decision("notify")), config)
        result = client.analyze(request("inspect"))
        self.assertEqual(result.action, "notify")
        self.assertEqual(result.model, "qwen3-vl-plus")
        self.assertAlmostEqual(result.estimated_cost_usd, 0.0025)
        payload = json.loads(calls[0][0].data)
        self.assertFalse(payload["enable_thinking"])
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["max_tokens"], 321)
        self.assertEqual(calls[0][1], 12)

    def test_custom_attend_model_and_prices(self) -> None:
        client, calls = self.client(completion(decision("inspect")), ModelConfig(
            attend_model="qwen3-vl-flash", attend_prices=ModelPrices(0.1, 0.3),
        ))
        result = client.analyze(request())
        self.assertEqual(result.action, "inspect")
        self.assertAlmostEqual(result.estimated_cost_usd, 0.001)
        self.assertFalse(json.loads(calls[0][0].data)["enable_thinking"])
        self.assertEqual(json.loads(calls[0][0].data)["response_format"], {"type": "json_object"})

    def test_unsupported_custom_model_uses_prompt_and_local_json_validation(self) -> None:
        client, calls = self.client(completion(), ModelConfig(attend_model="custom-vision-instruct"))
        self.assertEqual(client.analyze(request()).action, "observe")
        payload = json.loads(calls[0][0].data)
        self.assertNotIn("response_format", payload)
        self.assertNotIn("enable_thinking", payload)
        self.assertIn("JSON", payload["messages"][0]["content"])
        invalid, _ = self.client(completion("ordinary prose"), ModelConfig(attend_model="custom-vision-instruct"))
        with self.assertRaises(ModelResponseError):
            invalid.analyze(request())

    def test_missing_usage_remains_unknown(self) -> None:
        client, _ = self.client(completion(usage=False))
        result = client.analyze(request())
        self.assertIsNone(result.usage)
        self.assertIsNone(result.estimated_cost_usd)

    def test_all_permitted_actions_and_recheck_bounds(self) -> None:
        for stage, action, delay in (
            ("attend", "observe", None), ("attend", "inspect", None),
            ("attend", "uncertain", None), ("attend", "recheck", 0.5),
            ("inspect", "notify", None), ("inspect", "uncertain", None),
            ("inspect", "observe", None), ("inspect", "recheck", 60),
        ):
            with self.subTest(stage=stage, action=action):
                client, _ = self.client(completion(decision(action, delay)))
                self.assertEqual(client.analyze(request(stage)).action, action)

    def test_out_of_order_future_and_invalid_timestamps_fail_before_transport(self) -> None:
        cases = (
            (FrameSample(2.0, JPEG), FrameSample(1.0, JPEG)),
            (FrameSample(1.0, JPEG), FrameSample(1.0, JPEG)),
            (FrameSample(2.1, JPEG),), (FrameSample(float("nan"), JPEG),),
            (FrameSample(-1.0, JPEG),), (FrameSample(True, JPEG),),
        )
        for frames in cases:
            with self.subTest(frames=frames):
                client, calls = self.client(completion())
                with self.assertRaises(ValueError):
                    client.analyze(replace(request(), frames=frames))
                self.assertEqual(calls, [])

    def test_future_or_oversized_memory_is_rejected(self) -> None:
        for memory in (
            SceneMemory(2.1, "Future observation"), SceneMemory(-1, "Invalid"),
            SceneMemory(1, "a" * 6001), SceneMemory(float("inf"), "Invalid"),
        ):
            with self.subTest(memory=memory):
                client, calls = self.client(completion())
                with self.assertRaises(ValueError):
                    client.analyze(replace(request(), memory=memory))
                self.assertEqual(calls, [])

    def test_input_bounds_and_invalid_jpeg(self) -> None:
        cases = (
            (ModelConfig(), replace(request(), goal="")),
            (ModelConfig(), replace(request(), goal="a" * 2001)),
            (ModelConfig(), replace(request(), frames=())),
            (ModelConfig(max_frames=1), request()),
            (ModelConfig(max_frame_bytes=2), request()),
            (ModelConfig(max_image_bytes=len(JPEG)), request()),
            (ModelConfig(), replace(request(), frames=(FrameSample(1, b"bad"),))),
            (ModelConfig(), replace(request(), observed_until_seconds=float("nan"))),
            (ModelConfig(), replace(request(), stage="other")),
        )
        for config, value in cases:
            with self.subTest(config=config, request=value):
                client, calls = self.client(completion(), config)
                with self.assertRaises(ValueError):
                    client.analyze(value)
                self.assertEqual(calls, [])

    def test_missing_key_or_key_with_whitespace_never_calls_transport(self) -> None:
        for key in ("", "abc\ndef"):
            with self.subTest(key="empty" if not key else "invalid"), patch.dict(os.environ, {"ALIBABA_API_KEY": key}):
                client, calls = self.client(completion())
                with self.assertRaises(ModelTransportError) as caught:
                    client.analyze(request())
                self.assertNotIn(SECRET, str(caught.exception))
                self.assertEqual(calls, [])

    def test_invalid_decisions_never_become_success_and_preserve_billed_usage(self) -> None:
        cases = []
        cases.extend(("attend", decision("recheck", delay)) for delay in (None, 0, 61, True, "5"))
        cases.extend(("attend", value) for value in (
            decision("notify"), decision("arbitrary"), decision("observe", 5),
            {**decision(), "evidence": ""}, {**decision(), "event_summary": " "},
            {**decision(), "evidence": "a" * 3001},
            {**decision(), "event_summary": "a" * 1601},
            {**decision(), "extra": True}, {"action": "observe"},
            "```json\n" + json.dumps(decision()) + "\n```",
            '{"action":"observe","action":"notify","evidence":"x","event_summary":"x","recheck_after_seconds":null}',
            '{"action":"recheck","evidence":"x","event_summary":"x","recheck_after_seconds":NaN}',
        ))
        cases.append(("inspect", decision("inspect")))
        for stage, value in cases:
            with self.subTest(stage=stage, decision=value):
                client, calls = self.client(completion(value))
                with self.assertRaises(ModelResponseError) as caught:
                    client.analyze(request(stage))
                self.assertEqual(caught.exception.usage.total_tokens, 9000)
                self.assertEqual(caught.exception.latency_seconds, 0.25)
                self.assertEqual(len(calls), 1)

    def test_bad_envelopes_and_truncated_answers_are_rejected(self) -> None:
        replies = (
            b"not json", b'{"choices":[],"choices":[]}',
            b'{"error":{"message":"synthetic-api-key-not-real"}}',
            b'{"choices":[]}', b'[]', completion(finish="length"),
            completion(finish="content_filter"),
            json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": []}}]}).encode(),
            json.dumps({"choices": [{"finish_reason": "stop", "message": {"refusal": "cannot answer", "content": "{}"}}]}).encode(),
        )
        for reply in replies:
            with self.subTest(reply=reply):
                client, calls = self.client(reply)
                with self.assertRaises(ModelResponseError) as caught:
                    client.analyze(request())
                self.assertNotIn(SECRET, str(caught.exception))
                self.assertEqual(len(calls), 1)

    def test_invalid_usage_is_not_reported_as_valid_measurement(self) -> None:
        for usage in (
            {}, [], {"prompt_tokens": True, "completion_tokens": 2},
            {"prompt_tokens": -1, "completion_tokens": 2},
            {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 4},
            {"prompt_tokens": 0, "completion_tokens": 1, "total_tokens": True},
        ):
            with self.subTest(usage=usage):
                client, _ = self.client(completion(usage=usage))
                with self.assertRaises(ModelResponseError):
                    client.analyze(request())

    def test_large_response_rejected_even_with_custom_transport(self) -> None:
        client, _ = self.client(b"x" * 21, ModelConfig(max_response_bytes=20))
        with self.assertRaises(ModelResponseError):
            client.analyze(request())

    def test_transport_failure_is_sanitized_and_not_retried(self) -> None:
        calls = []

        def broken(http_request, timeout, limit):
            calls.append(http_request)
            raise RuntimeError(f"Failure with Authorization: {SECRET}")

        client = AlibabaVisionClient(transport=broken)
        with self.assertRaises(ModelTransportError) as caught:
            client.analyze(request())
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertEqual(caught.exception.stage, "attend")
        self.assertEqual(len(calls), 1)

    def test_config_rejects_unsafe_endpoint_and_invalid_limits(self) -> None:
        for endpoint in (
            "http://example.com/chat", "https://user:pass@example.com/chat",
            "https://example.com/chat?key=secret", "https://example.com/chat#secret", "https:///chat",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                ModelConfig(endpoint=endpoint)
        for kwargs in ({"timeout": 0}, {"timeout": float("nan")}, {"max_frames": True}, {"max_output_tokens": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ModelConfig(**kwargs)
        for rate in (-1, float("inf"), True):
            with self.assertRaises(ValueError):
                ModelPrices(rate, 0)
        with self.assertRaises(ValueError):
            AlibabaVisionClient(api_key_env="OTHER_KEY")

    def test_frame_repr_does_not_dump_image_bytes(self) -> None:
        self.assertNotIn("synthetic-jpeg", repr(FrameSample(1, JPEG)))

    def test_urllib_http_errors_do_not_leak_provider_echo_or_retry(self) -> None:
        from urllib.request import Request

        http_request = Request(ModelConfig().endpoint, data=b"{}")
        failure = HTTPError(http_request.full_url, 403, f"provider echoed {SECRET}", {}, io.BytesIO(SECRET.encode()))
        with patch("vision.stream_models.build_opener") as opener:
            opener.return_value.open.side_effect = failure
            with self.assertRaises(ModelTransportError) as caught:
                _post(http_request, 5, 100)
            self.assertEqual(str(caught.exception), "Model HTTP 403: access denied or quota unavailable.")
            self.assertEqual(opener.return_value.open.call_count, 1)
        with patch("vision.stream_models.build_opener") as opener:
            opener.return_value.open.side_effect = URLError(SECRET)
            with self.assertRaises(ModelTransportError) as caught:
                _post(http_request, 5, 100)
            self.assertNotIn(SECRET, str(caught.exception))

    def test_redirects_are_rejected_and_reads_are_bounded(self) -> None:
        from urllib.request import Request

        http_request = Request(ModelConfig().endpoint, data=b"{}")
        self.assertIsNone(_NoRedirect().redirect_request(http_request, None, 302, "", {}, "https://other.example"))
        response = io.BytesIO(b"x" * 30)
        with patch("vision.stream_models.build_opener") as opener:
            opener.return_value.open.return_value = response
            with self.assertRaises(ModelTransportError):
                _post(http_request, 5, 20)

    def test_http_404_exposes_only_allowlisted_code_and_configuration_hint(self) -> None:
        from urllib.request import Request

        for code, expected in (('model_not_found', 'model_not_found'),
                               ('WorkSpaceNotFound', 'WorkSpaceNotFound'),
                               (SECRET, None)):
            with self.subTest(code=code):
                body = json.dumps({'error': {'code': code, 'message': SECRET}}).encode()
                failure = HTTPError(ModelConfig().endpoint, 404, SECRET, {}, io.BytesIO(body))
                with patch('vision.stream_models.build_opener') as opener:
                    opener.return_value.open.side_effect = failure
                    with self.assertRaises(ModelHTTPError) as caught:
                        _post(Request(ModelConfig().endpoint), 5, 8192)
                self.assertEqual(caught.exception.status_code, 404)
                self.assertEqual(caught.exception.provider_code, expected)
                self.assertTrue(caught.exception.configuration_failure)
                self.assertNotIn(SECRET, str(caught.exception))
                self.assertEqual(opener.return_value.open.call_count, 1)

    def test_error_body_is_bounded_and_flat_provider_envelope_is_supported(self) -> None:
        from urllib.request import Request

        body = io.BytesIO(b'x' * 9000)
        failure = HTTPError(ModelConfig().endpoint, 404, SECRET, {}, body)
        with patch('vision.stream_models.build_opener') as opener:
            opener.return_value.open.side_effect = failure
            with self.assertRaises(ModelHTTPError) as caught:
                _post(Request(ModelConfig().endpoint), 5, 8192)
        self.assertIsNone(caught.exception.provider_code)
        body = json.dumps({'code': 'ModelNotFound', 'message': SECRET}).encode()
        failure = HTTPError(ModelConfig().endpoint, 404, SECRET, {}, io.BytesIO(body))
        with patch('vision.stream_models.build_opener') as opener:
            opener.return_value.open.side_effect = failure
            with self.assertRaises(ModelHTTPError) as caught:
                _post(Request(ModelConfig().endpoint), 5, 8192)
        self.assertEqual(caught.exception.provider_code, 'ModelNotFound')
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertFalse(ModelHTTPError('Rate limited', status_code=429).configuration_failure)
        self.assertFalse(ModelHTTPError('Server error', status_code=503).configuration_failure)


if __name__ == "__main__":
    unittest.main()
