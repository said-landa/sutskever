"""Causal scheduler acceptance checks; semantic results here are explicit fixtures.

These tests establish scheduling and state behavior, not a model's ability to
recognize an event. They need no camera, network access, API key, or video runtime.
"""

from __future__ import annotations

from dataclasses import asdict
import json
import unittest

from vision.stream_attention import AttentionConfig, AttentionController
from vision.stream_models import AnalysisResult
from vision.stream_video import FrameObservation


def frame(time: float, *, motion: float = 0.0) -> FrameObservation:
    return FrameObservation(
        time=time,
        frame_index=round(time * 10),
        jpeg=b"\xff\xd8fixture\xff\xd9",
        motion=motion,
    )


def result(stage: str, action: str = "observe", *, delay: float | None = None,
           summary: str = "Ordinary scene in the supplied images.") -> AnalysisResult:
    return AnalysisResult(
        action=action,
        evidence="Fixture evidence is limited to the submitted image timestamps.",
        event_summary=summary,
        recheck_after_seconds=delay,
        usage=None,
        latency_seconds=0.0,
        model="fixture",
        stage=stage,
    )


class AttentionAcceptanceTests(unittest.TestCase):
    def test_quiet_scene_still_gets_semantic_checks_and_independent_deep_audits(self):
        controller = AttentionController()
        seen = []
        for second in range(122):
            for plan in controller.observe(frame(float(second))):
                seen.append(plan)
                self.assertEqual(plan.observed_until, second)
                self.assertEqual(controller.complete(plan, result(plan.stage), second), [])
        attend = [plan.observed_until for plan in seen if plan.stage == "attend"]
        inspect = [plan.observed_until for plan in seen if plan.stage == "inspect"]
        self.assertGreaterEqual(len(attend), 5)
        self.assertGreaterEqual(len(inspect), 2)
        # A deep review can substitute for the next lightweight semantic check.
        reviews = sorted({plan.observed_until for plan in seen})
        self.assertLessEqual(max(b - a for a, b in zip(reviews, reviews[1:])), 21)
        self.assertLessEqual(inspect[0], 61)
        self.assertEqual(controller.summary()["notifications"], [])

    def test_pending_analysis_does_not_stop_observations_or_duplicate_same_stage(self):
        controller = AttentionController(AttentionConfig(inspect_interval=100.0))
        self.assertEqual(controller.observe(frame(0.0)), [])
        [plan] = controller.observe(frame(1.0))
        self.assertEqual(plan.stage, "attend")
        for second in range(2, 11):
            self.assertEqual(controller.observe(frame(float(second), motion=0.5)), [])
        state = controller.summary()
        self.assertEqual(state["observations"], 11)
        self.assertEqual(state["calls"]["attend"], 1)
        self.assertEqual(state["recent_interpretations"], [])
        self.assertEqual(state["notifications"], [])
        controller.complete(plan, result("attend"), 10.0)
        self.assertTrue(controller.summary()["unresolved"])

    def test_stationary_unresolved_situation_is_rechecked_by_timer(self):
        controller = AttentionController(AttentionConfig(
            attend_interval=100.0, inspect_interval=100.0, minimum_call_interval=0.5,
        ))
        controller.observe(frame(0.0))
        [initial] = controller.observe(frame(1.0))
        controller.complete(initial, result("attend", "recheck", delay=2.0), 1.0)
        self.assertEqual(controller.observe(frame(2.0)), [])
        [followup] = controller.observe(frame(3.0))
        self.assertEqual(followup.stage, "attend")
        self.assertIn("unresolved_recheck", followup.reasons)
        self.assertTrue(controller.summary()["unresolved"])

    def test_only_deep_result_notifies_and_notification_records_arrival_time(self):
        controller = AttentionController(AttentionConfig(minimum_call_interval=0.5))
        controller.observe(frame(0.0))
        [attend] = controller.observe(frame(1.0, motion=0.5))
        self.assertEqual(controller.summary()["notifications"], [])
        self.assertEqual(controller.complete(attend, result("attend", "inspect"), 1.0), [])
        [inspect] = controller.observe(frame(1.5))
        self.assertEqual(inspect.stage, "inspect")
        self.assertEqual(controller.summary()["notifications"], [])
        [notification] = controller.complete(
            inspect, result("inspect", "notify", summary="A visible event warrants attention."), 4.0,
        )
        self.assertEqual(notification.observed_until, 1.5)
        self.assertEqual(notification.available_at, 4.0)
        self.assertEqual(notification.plan_id, inspect.id)

    def test_old_ordinary_reply_cannot_clear_newer_semantic_uncertainty(self):
        controller = AttentionController(AttentionConfig(
            inspect_interval=2.0, attend_interval=100.0, minimum_call_interval=0.5,
        ))
        controller.observe(frame(0.0))
        [old_attend] = controller.observe(frame(1.0))
        [new_inspect] = controller.observe(frame(2.0))
        self.assertEqual(new_inspect.stage, "inspect")
        controller.complete(new_inspect, result("inspect", "recheck", delay=4.0), 2.0)
        controller.complete(old_attend, result("attend", "observe"), 2.0)
        self.assertTrue(controller.summary()["unresolved"])
        plans = []
        for second in range(3, 7):
            plans.extend(controller.observe(frame(float(second))))
        self.assertTrue(any("unresolved_recheck" in plan.reasons for plan in plans))

    def test_old_ordinary_reply_cannot_clear_newer_inspection_failure(self):
        controller = AttentionController(AttentionConfig(
            inspect_interval=2.0, attend_interval=100.0, minimum_call_interval=0.5,
        ))
        controller.observe(frame(0.0))
        [old_attend] = controller.observe(frame(1.0))
        [new_inspect] = controller.observe(frame(2.0))
        controller.fail(new_inspect, "Model response unavailable.", 2.0)
        controller.complete(old_attend, result("attend", "observe"), 2.0)
        self.assertTrue(controller.summary()["unresolved"])
        self.assertEqual(controller.summary()["health"], "degraded")
        plans = []
        for second in range(3, 8):
            plans.extend(controller.observe(frame(float(second))))
        self.assertTrue(any("unresolved_recheck" in plan.reasons for plan in plans))

    def test_old_ordinary_reply_cannot_close_newer_notified_situation(self):
        controller = AttentionController(AttentionConfig(
            inspect_interval=2.0, attend_interval=100.0, minimum_call_interval=0.5,
        ))
        controller.observe(frame(0.0))
        [old_attend] = controller.observe(frame(1.0))
        [new_inspect] = controller.observe(frame(2.0))
        controller.complete(new_inspect, result("inspect", "notify"), 2.0)
        controller.complete(old_attend, result("attend", "observe"), 2.0)
        self.assertTrue(controller.summary()["unresolved"])
        self.assertEqual(len(controller.summary()["notifications"]), 1)

    def test_same_event_is_suppressed_during_cooldown_but_can_notify_later(self):
        controller = AttentionController(AttentionConfig(
            inspect_interval=2.0, attend_interval=100.0, minimum_call_interval=0.5,
            notification_cooldown=10.0,
        ))
        notifications = []
        for second in range(15):
            for plan in controller.observe(frame(float(second))):
                decision = result(plan.stage, "notify" if plan.stage == "inspect" else "observe",
                                  summary="The same visible event.")
                notifications.extend(controller.complete(plan, decision, float(second)))
        self.assertEqual(len(notifications), 2)
        self.assertGreaterEqual(notifications[1].available_at - notifications[0].available_at, 10.0)

    def test_failure_remains_unresolved_and_retries_without_fabricated_alert(self):
        controller = AttentionController(AttentionConfig(
            inspect_interval=100.0, attend_interval=100.0, minimum_call_interval=0.5,
        ))
        controller.observe(frame(0.0))
        [plan] = controller.observe(frame(1.0))
        controller.fail(plan, "Model quota unavailable.", 1.0)
        state = controller.summary()
        self.assertEqual(state["health"], "degraded")
        self.assertTrue(state["unresolved"])
        self.assertEqual(state["notifications"], [])
        self.assertEqual(state["recent_interpretations"], [])
        followups = []
        for second in range(2, 7):
            followups.extend(controller.observe(frame(float(second))))
        self.assertTrue(any("unresolved_recheck" in plan.reasons for plan in followups))

    def test_call_budget_is_hard_limit_and_exhaustion_is_explicit(self):
        controller = AttentionController(AttentionConfig(max_calls=1))
        controller.observe(frame(0.0))
        [plan] = controller.observe(frame(1.0))
        controller.complete(plan, result("attend"), 1.0)
        for second in range(2, 80):
            self.assertEqual(controller.observe(frame(float(second))), [])
        state = controller.summary()
        self.assertEqual(sum(state["calls"].values()), 1)
        self.assertEqual(state["health"], "budget_exhausted")
        self.assertGreater(state["denied"], 0)

    def test_future_frames_cannot_change_prior_requests(self):
        def replay(end: int):
            controller = AttentionController(AttentionConfig(
                attend_interval=3.0, inspect_interval=10.0, minimum_call_interval=0.5,
            ))
            prefix = []
            for second in range(end + 1):
                movement = 0.4 if second in (2, 5, 11) else 0.0
                for plan in controller.observe(frame(float(second), motion=movement)):
                    if second <= 6:
                        prefix.append(asdict(plan))
                    controller.complete(plan, result(plan.stage), float(second))
            return prefix
        self.assertEqual(replay(6), replay(30))

    def test_unknown_or_duplicate_completion_cannot_modify_state(self):
        controller = AttentionController()
        controller.observe(frame(0.0))
        [plan] = controller.observe(frame(1.0))
        with self.assertRaises(ValueError):
            controller.complete(plan, result("attend"), 0.5)
        self.assertEqual(controller.summary()["recent_interpretations"], [])
        controller.complete(plan, result("attend"), 1.0)
        previous = controller.summary()
        with self.assertRaises(ValueError):
            controller.complete(plan, result("attend"), 1.0)
        self.assertEqual(controller.summary(), previous)

    def test_memory_remains_bounded_valid_json_and_excludes_frame_payload(self):
        controller = AttentionController(AttentionConfig(
            attend_interval=1.0, inspect_interval=100.0, minimum_call_interval=0.5,
        ))
        for second in range(30):
            for plan in controller.observe(frame(float(second))):
                controller.complete(plan, result(plan.stage, summary="Long context " * 100), float(second))
        memory = controller.memory_text()
        self.assertLessEqual(len(memory), 6000)
        decoded = json.loads(memory)
        self.assertIn("scene", decoded)
        self.assertIn("recent_interpretations", decoded)
        self.assertNotIn("jpeg", memory)
        self.assertNotIn("base64", memory)


if __name__ == "__main__":
    unittest.main()
