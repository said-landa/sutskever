"""Causal, provider-independent attention scheduling and bounded scene memory.

The controller never claims that motion means danger. Only an Inspect result can
produce a notification. Thresholds are experiment settings, not calibrated risk.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Any


@dataclass(frozen=True)
class AttentionConfig:
    startup_seconds: float = 1.0
    attend_interval: float = 20.0
    inspect_interval: float = 60.0
    minimum_call_interval: float = 3.0
    motion_threshold: float = 0.08
    appearance_threshold: float = 0.12
    uncertainty_threshold: float = 0.65
    retry_seconds: float = 5.0
    notification_cooldown: float = 60.0
    max_calls: int = 100
    max_history: int = 12

    def __post_init__(self):
        for name in ('startup_seconds', 'attend_interval', 'inspect_interval',
                     'minimum_call_interval', 'retry_seconds', 'notification_cooldown'):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be finite and positive')
        for name in ('motion_threshold', 'appearance_threshold', 'uncertainty_threshold'):
            if not 0 < getattr(self, name) <= 1:
                raise ValueError(f'{name} must be in (0, 1]')
        if self.max_calls < 1 or self.max_history < 1:
            raise ValueError('Call and memory limits must be positive')


@dataclass(frozen=True)
class InspectionPlan:
    id: int
    stage: str
    observed_until: float
    reasons: tuple[str, ...]
    scene_revision: int
    memory_text: str


@dataclass(frozen=True)
class Notification:
    id: str
    observed_until: float
    available_at: float
    summary: str
    evidence: str
    plan_id: int


class AttentionController:
    def __init__(self, config: AttentionConfig | None = None):
        self.config = config or AttentionConfig()
        self.now = -1.0
        self.started_at: float | None = None
        self.revision = 0
        self.observations = 0
        self.pending: dict[str, InspectionPlan] = {}
        self.call_counts = {'attend': 0, 'inspect': 0}
        self.last_call = {'attend': -math.inf, 'inspect': -math.inf}
        self.last_semantic = -math.inf
        self.last_inspection = -math.inf
        self.recheck_at = math.inf
        self.inspect_requested = False
        self.unresolved = False
        self.health = 'unassessed'
        self.errors: deque[dict] = deque(maxlen=self.config.max_history)
        self.history: deque[dict] = deque(maxlen=self.config.max_history)
        self.scene: dict[str, Any] = {}
        self.notifications: deque[Notification] = deque(maxlen=100)
        self.notification_times: dict[str, float] = {}
        self.denied = 0
        self._active_signals: set[str] = set()
        self._latched_reasons: set[str] = set()
        self._next_id = 1
        self._semantic_watermark = (-1.0, -1)
        self._deep_unresolved = False
        self._final_attend_submitted = False

    def observe(self, frame) -> list[InspectionPlan]:
        if not math.isfinite(frame.time) or frame.time < 0 or frame.time <= self.now:
            raise ValueError('Observations must have strictly increasing finite timestamps')
        self.now = frame.time
        self.started_at = self.now if self.started_at is None else self.started_at
        self.observations += 1
        signals = set()
        for field, threshold, reason in (
            ('motion', self.config.motion_threshold, 'movement'),
            ('appearance_change', self.config.appearance_threshold, 'appearance_change'),
            ('uncertainty', self.config.uncertainty_threshold, 'visibility_uncertainty'),
        ):
            value = getattr(frame, field)
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f'Invalid observation {field}')
            if value >= threshold:
                signals.add(reason)
        old_tracks = {str(t.get('id')) for t in self.scene.get('tracks', [])}
        # Preserve semantic detections before generic foreground fragments when
        # the bounded scene-state list is full.
        ordered = sorted(frame.tracks, key=lambda t: t.get('source') != 'coco')
        tracks = [dict(t) for t in ordered[:16]]
        new_tracks = {str(t.get('id')) for t in tracks}
        if self.observations > 1 and old_tracks != new_tracks:
            signals.add('track_change')
        # A continuing moving scene also invalidates an old "nothing happened"
        # result; a late reply must not clear newer uncertainty.
        if signals:
            self.revision += 1
            self.unresolved = True
            self._latched_reasons.update(signals)
        self._active_signals = signals
        self.scene = {'through_seconds': self.now, 'tracks': tracks,
                      'motion': frame.motion, 'appearance_change': frame.appearance_change,
                      'uncertainty': frame.uncertainty}
        return self._schedule()

    def _schedule(self) -> list[InspectionPlan]:
        if self.started_at is None or self.now - self.started_at < self.config.startup_seconds:
            return []
        plans = []
        # Independent deep audits keep the small-model gate from being absolute.
        last_deep = max(self.last_inspection, self.last_call['inspect'], self.started_at)
        if self.inspect_requested or self.now - last_deep >= self.config.inspect_interval:
            reasons = ('semantic_escalation',) if self.inspect_requested else ('deep_audit',)
            plan = self._plan('inspect', reasons)
            if plan:
                plans.append(plan)
                self.inspect_requested = False
        last_attend = max(self.last_semantic, self.last_call['attend'])
        reasons = set(self._latched_reasons)
        if not math.isfinite(last_attend):
            reasons.add('initial_context')
        elif self.now - last_attend >= self.config.attend_interval:
            reasons.add('semantic_check')
        if self.now >= self.recheck_at:
            reasons.add('unresolved_recheck')
        if reasons:
            plan = self._plan('attend', tuple(sorted(reasons)))
            if plan:
                plans.append(plan)
                self._latched_reasons.clear()
                self.recheck_at = math.inf
        return plans

    def _plan(self, stage: str, reasons: tuple[str, ...], *, final: bool = False) -> InspectionPlan | None:
        if stage in self.pending or (not final and self.now - self.last_call[stage] < self.config.minimum_call_interval):
            return None
        if sum(self.call_counts.values()) >= self.config.max_calls:
            self.health = 'budget_exhausted'
            self.denied += 1
            return None
        plan = InspectionPlan(self._next_id, stage, self.now, reasons, self.revision, self.memory_text())
        self._next_id += 1
        self.pending[stage] = plan
        self.call_counts[stage] += 1
        self.last_call[stage] = self.now
        return plan

    def complete(self, plan: InspectionPlan, result, completed_at: float) -> list[Notification]:
        self._validate_completion(plan, completed_at)
        if result.stage != plan.stage:
            raise ValueError('Result stage does not match plan')
        allowed = {'observe', 'recheck', 'uncertain', 'inspect'} if plan.stage == 'attend' else {'observe', 'recheck', 'uncertain', 'notify'}
        if result.action not in allowed:
            raise ValueError('Action is not permitted for this stage')
        delay = result.recheck_after_seconds if result.action == 'recheck' else self.config.retry_seconds
        if result.action in ('recheck', 'uncertain') and (delay is None or not math.isfinite(delay) or not 0.5 <= delay <= 60):
            raise ValueError('Invalid recheck delay')
        watermark = (plan.observed_until, plan.id)
        self.pending.pop(plan.stage)
        self.health = 'running' if not self.errors else 'degraded'
        self.history.append({'observed_until': plan.observed_until, 'available_at': completed_at,
                             'stage': plan.stage, 'action': result.action,
                             'summary': result.event_summary[:1200], 'evidence': result.evidence[:1200]})
        if result.action != 'observe':
            self._semantic_watermark = max(self._semantic_watermark, watermark)
            if plan.stage == 'inspect':
                self._deep_unresolved = True
        self.last_semantic = max(self.last_semantic, plan.observed_until)
        if plan.stage == 'inspect':
            self.last_inspection = max(self.last_inspection, plan.observed_until)
        if result.action == 'inspect':
            self.inspect_requested = True
            self.unresolved = True
        elif result.action in ('recheck', 'uncertain'):
            self.recheck_at = min(self.recheck_at, completed_at + delay)
            self.unresolved = True
            self._semantic_watermark = max(self._semantic_watermark, watermark)
            if result.action == 'uncertain' and plan.stage == 'attend':
                self.inspect_requested = True
        elif result.action == 'observe' and plan.scene_revision == self.revision and watermark >= self._semantic_watermark and (plan.stage == 'inspect' or not self._deep_unresolved):
            self.unresolved = False
            self._deep_unresolved = False
            self.recheck_at = math.inf
            self._semantic_watermark = watermark
        if result.action != 'notify':
            return []
        self.unresolved = True
        fingerprint = hashlib.sha256(' '.join(result.event_summary.lower().split()).encode()).hexdigest()[:20]
        previous = self.notification_times.get(fingerprint, -math.inf)
        if completed_at - previous < self.config.notification_cooldown:
            return []
        self.notification_times = {k: v for k, v in self.notification_times.items()
                                   if completed_at - v < self.config.notification_cooldown}
        self.notification_times[fingerprint] = completed_at
        notification = Notification(fingerprint + f'-{plan.id}', plan.observed_until,
                                    completed_at, result.event_summary, result.evidence, plan.id)
        self.notifications.append(notification)
        return [notification]

    def fail(self, plan: InspectionPlan, error: str, completed_at: float):
        self._validate_completion(plan, completed_at)
        self.pending.pop(plan.stage)
        self._semantic_watermark = max(self._semantic_watermark, (plan.observed_until, plan.id))
        if plan.stage == 'inspect':
            self._deep_unresolved = True
        self.errors.append({'plan_id': plan.id, 'at': completed_at, 'error': error[:300]})
        self.health = 'degraded'
        self.unresolved = True
        self.recheck_at = min(self.recheck_at, completed_at + self.config.retry_seconds)

    def _validate_completion(self, plan: InspectionPlan, completed_at: float):
        if self.pending.get(plan.stage) != plan:
            raise ValueError('Unknown or already completed plan')
        if not math.isfinite(completed_at) or completed_at < plan.observed_until or completed_at < self.now:
            raise ValueError('A completion cannot arrive before observed evidence or current time')

    def memory_text(self) -> str:
        history = list(self.history)
        payload = {'scene': self.scene, 'unresolved': self.unresolved,
                   'recent_interpretations': history}
        text = json.dumps(payload, separators=(',', ':'))
        while len(text) > 6000 and history:
            history.pop(0)
            text = json.dumps(payload, separators=(',', ':'))
        if len(text) > 6000:
            payload['scene'] = {k: v for k, v in self.scene.items() if k != 'tracks'}
            text = json.dumps(payload, separators=(',', ':'))
        return text

    def drain_plans(self) -> list[InspectionPlan]:
        """Review the final unseen tail once, without inventing future imagery."""
        if self.inspect_requested:
            plan = self._plan('inspect', ('semantic_escalation_after_input',), final=True)
            if plan:
                self.inspect_requested = False
                return [plan]
        deep_current = self.last_inspection >= self.now or (
            'inspect' in self.pending and self.pending['inspect'].observed_until >= self.now)
        if not deep_current and not self._final_attend_submitted and self.last_call['attend'] < self.now:
            plan = self._plan('attend', ('final_unreviewed_context',), final=True)
            if plan:
                self._final_attend_submitted = True
                return [plan]
        return []

    def summary(self) -> dict:
        return {'observed_until': self.now, 'observations': self.observations, 'health': self.health,
                'unresolved': self.unresolved, 'calls': dict(self.call_counts), 'denied': self.denied,
                'deferred_inspection': self.inspect_requested,
                'pending': {k: v.id for k, v in self.pending.items()},
                'recent_interpretations': list(self.history), 'errors': list(self.errors),
                'notifications': [asdict(n) for n in self.notifications]}
