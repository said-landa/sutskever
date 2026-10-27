"""Replay a video through Watch / Attend / Inspect without future-frame access.

Examples (from the repository root):
  .venv/bin/python -m vision.stream_replay data/videos/box-falls.MOV
  .venv/bin/python -m vision.stream_replay clip.mp4 --backend alibaba --ask-key

Default is a real local Watch pass with no semantic requests or model charges.
Alibaba mode is paced at source time, reads ALIBABA_API_KEY (or secure getpass),
and records actual usage. It never reads credentials from the iPhone or RunPod.
"""
from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import asdict
import getpass
import html
import json
import math
import os
from pathlib import Path
import time

from vision.stream_attention import AttentionConfig, AttentionController, InspectionPlan
from vision.stream_models import (AlibabaVisionClient, FrameSample, ModelConfig,
                                  ModelError, ModelHTTPError, ModelRequest, ModelPrices, SceneMemory)
from vision.stream_video import FrameRingBuffer, VideoSource


class ReplayExperiment:
    """One bounded in-flight call per stage; observation never waits for HTTP.

    Input consumption and completion application happen on the main thread. JPEG
    evidence is pinned at submission, separate from the rolling buffer. A result
    arriving after input ends remains a late result, never an earlier detection.
    """
    def __init__(self, goal: str, output: Path, *, client=None,
                 config: AttentionConfig | None = None, realtime: bool = False,
                 lookback: float = 10.0, max_frames: int = 16, ring_seconds: float = 60.0,
                 ring_bytes: int = 96 * 1024 * 1024, clock=time.monotonic, sleeper=time.sleep):
        if not goal.strip() or len(goal) > 2000:
            raise ValueError('Goal must contain 1–2000 characters')
        if not math.isfinite(lookback) or lookback <= 0 or not 2 <= max_frames <= 24:
            raise ValueError('Lookback must be positive and evidence frame count 2–24')
        self.goal, self.output, self.client = goal, Path(output), client
        self.controller = AttentionController(config)
        self.ring = FrameRingBuffer(ring_seconds, ring_bytes, max_frames=7200)
        self.lookback, self.max_frames, self.realtime = lookback, max_frames, realtime
        self.clock, self.sleeper = clock, sleeper
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='vision-model')
        self.jobs: dict[int, tuple[InspectionPlan, Future, tuple, float]] = {}
        self.anchors: list = []
        self.start_wall = 0.0
        self.last_observed = 0.0
        self.eof = False
        self.events: list[dict] = []
        self.usage = {'input_tokens': 0, 'output_tokens': 0, 'responses_with_usage': 0,
                      'responses_without_usage': 0}
        self.latencies: list[float] = []
        self.total_observations = 0
        self.peak_ring_bytes = 0
        self.cancelled = False
        self._event_stream = None
        self.buffered_frames = 0
        self.result_time = 0.0
        self.cost_estimates: list[float] = []
        self.unknown_cost_responses = 0
        self.watch_wall_seconds = 0.0
        self.max_processing_lag_seconds = 0.0

    def buffer_frame(self, frame):
        """Receive every source frame independently of the neural sample cadence."""
        self.ring.append(frame)
        self.buffered_frames += 1
        self.peak_ring_bytes = max(self.peak_ring_bytes, self.ring.byte_size)

    def log(self, event: dict):
        # Requests, headers, API keys, raw provider bodies and base64 never enter
        # these logs. Payload imagery remains in memory until the job completes.
        if len(self.events) < 1000:
            self.events.append(event)
        if self._event_stream:
            self._event_stream.write(json.dumps(event, allow_nan=False) + '\n')
            self._event_stream.flush()

    def run(self, source, *, metadata: dict | None = None) -> dict:
        self.output.mkdir(parents=True, exist_ok=False)
        self.start_wall = self.clock()
        status = 'completed'
        self._event_stream = (self.output / 'events.jsonl').open('w')
        try:
            for frame in source:
                if self.realtime:
                    delay = frame.time - (self.clock() - self.start_wall)
                    if delay > 0:
                        self.sleeper(delay)
                self.last_observed = frame.time
                self.max_processing_lag_seconds = max(self.max_processing_lag_seconds,
                                                     self.clock() - self.start_wall - frame.time)
                if self.ring.newest_time != frame.time:
                    self.ring.append(frame)
                self.peak_ring_bytes = max(self.peak_ring_bytes, self.ring.byte_size)
                self.total_observations += 1
                self.poll()
                for plan in self.controller.observe(frame):
                    self.submit(plan)
                if self.total_observations % 5 == 1:
                    self.log({'type': 'watch', 'at': frame.time, 'motion': frame.motion,
                              'appearance_change': frame.appearance_change,
                              'uncertainty': frame.uncertainty, 'tracks': frame.tracks[:16],
                              'pending': list(self.controller.pending)})
            self.watch_wall_seconds = self.clock() - self.start_wall
            self.eof = True
            for plan in self.controller.drain_plans():
                self.submit(plan)
            # Drain already-submitted work and any final semantic escalation;
            # never fabricate additional video frames or periodic future checks.
            while self.jobs:
                wait([j[1] for j in self.jobs.values()], timeout=0.2, return_when=FIRST_COMPLETED)
                self.poll()
                for plan in self.controller.drain_plans():
                    self.submit(plan)
            if self.client and (self.controller.errors or self.controller.denied or self.controller.inspect_requested):
                status = 'degraded'
        except KeyboardInterrupt:
            status, self.cancelled = 'cancelled', True
            self.log({'type': 'cancelled', 'at': self.last_observed})
        except Exception:
            status = 'failed'
            self.log({'type': 'error', 'at': self.last_observed,
                      'message': 'Replay stopped; inspect the terminal error. No safety assessment is available.'})
            raise
        finally:
            if status in ('cancelled', 'failed') and callable(getattr(source, 'close', None)):
                source.close()
            if not self.watch_wall_seconds:
                self.watch_wall_seconds = self.clock() - self.start_wall
            self.executor.shutdown(wait=not self.cancelled, cancel_futures=True)
            # A second stage may already have been sent before the fatal reply.
            # Account for its completed usage without scheduling any more work.
            while status == 'failed' and self.jobs:
                try:
                    self.poll()
                except ModelHTTPError:
                    pass
            summary = self.report(status, metadata)
            self._event_stream.close()
            self._event_stream = None
            (self.output / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False))
            self.write_html(summary)
        return summary

    def submit(self, plan: InspectionPlan):
        lookback = self.lookback
        if 'deep_audit' in plan.reasons:
            lookback = self.ring.duration_seconds
        elif 'semantic_check' in plan.reasons:
            lookback = max(lookback, self.controller.config.attend_interval + 2)
        frame_limit = min(self.max_frames, 6) if plan.stage == 'attend' else self.max_frames
        frames = self.ring.snapshot(plan.observed_until, lookback, frame_limit)
        # Context anchors are real earlier images, rather than a generated
        # summary alone. Their byte count is independently bounded below.
        by_time = {f.time: f for f in [*self.anchors, *frames] if f.time <= plan.observed_until}
        selected = sorted(by_time.values(), key=lambda f: f.time)
        if len(selected) > frame_limit:
            selected = [selected[0], *selected[-(frame_limit - 1):]]
        while sum(len(f.jpeg) for f in selected) > 8_000_000 and len(selected) > 2:
            selected.pop(1)
        self.log({'type': 'request' if self.client else 'planned_request', 'id': plan.id,
                  'stage': plan.stage, 'observed_until': plan.observed_until,
                  'reasons': list(plan.reasons), 'frame_times': [f.time for f in selected],
                  'image_bytes': sum(len(f.jpeg) for f in selected),
                  'after_input_ended': self.eof})
        if not self.client:
            self.controller.fail(plan, 'Watch-only mode: semantic inference was not executed', self.last_observed)
            return
        request = ModelRequest(plan.stage, self.goal,
                               tuple(FrameSample(f.time, f.jpeg) for f in selected),
                               plan.observed_until, SceneMemory(plan.observed_until, plan.memory_text))
        submitted_at = max(plan.observed_until, self.result_time,
                           self.clock() - self.start_wall if self.realtime else 0)
        self.jobs[plan.id] = (plan, self.executor.submit(self.client.analyze, request), tuple(selected), submitted_at)

    def poll(self):
        for identifier, (plan, future, frames, submitted_at) in list(self.jobs.items()):
            if not future.done():
                continue
            del self.jobs[identifier]
            arrival = max(self.last_observed, self.controller.now,
                          self.clock() - self.start_wall if self.realtime else self.last_observed)
            try:
                result = future.result()
                arrival = max(arrival, self.result_time, submitted_at + result.latency_seconds)
                self.result_time = arrival
                notifications = self.controller.complete(plan, result, arrival)
                self.latencies.append(result.latency_seconds)
                self.add_usage(result.usage)
                if result.estimated_cost_usd is None:
                    self.unknown_cost_responses += 1
                else:
                    self.cost_estimates.append(result.estimated_cost_usd)
                self.log({'type': 'result', 'id': plan.id, 'stage': plan.stage,
                          'observed_until': plan.observed_until, 'available_at': arrival,
                          'latency_seconds': result.latency_seconds, 'model': result.model,
                          'action': result.action, 'evidence': result.evidence,
                          'event_summary': result.event_summary,
                          'usage': None if result.usage is None else asdict(result.usage),
                          'estimated_cost_usd': result.estimated_cost_usd,
                          'after_input_ended': self.eof})
                if result.action in ('inspect', 'recheck', 'notify', 'uncertain') and frames:
                    self.anchors.extend((frames[0], frames[-1]))
                    self.anchors = sorted({f.time: f for f in self.anchors}.values(), key=lambda f: f.time)[-8:]
                    while sum(len(f.jpeg) for f in self.anchors) > 4_000_000:
                        self.anchors.pop(0)
                elif result.action == 'observe' and not self.controller.unresolved:
                    self.anchors.clear()
                for notification in notifications:
                    event = {'type': 'notification', **asdict(notification),
                             'delivery': 'local_outbox', 'after_input_ended': self.eof}
                    self.log(event)
                    with (self.output / 'notifications.jsonl').open('a') as stream:
                        stream.write(json.dumps(event) + '\n')
            except Exception as error:
                self.unknown_cost_responses += 1
                if isinstance(error, ModelError):
                    message = str(error)
                    if error.usage:
                        self.add_usage(error.usage)
                else:
                    message = 'Model execution or decision validation failed'
                if self.controller.pending.get(plan.stage) == plan:
                    self.controller.fail(plan, message, arrival)
                self.log({'type': 'model_error', 'id': plan.id, 'stage': plan.stage,
                          'available_at': arrival, 'message': message,
                          'model': error.model if isinstance(error, ModelError) else None,
                          'http_status': error.status_code if isinstance(error, ModelHTTPError) else None,
                          'provider_code': error.provider_code if isinstance(error, ModelHTTPError) else None})
                if isinstance(error, ModelHTTPError) and error.configuration_failure:
                    # A new observation cannot repair credentials, permissions,
                    # a missing model or an invalid route. Stop without retries.
                    raise

    def add_usage(self, usage):
        if usage is None:
            self.usage['responses_without_usage'] += 1
        else:
            self.usage['responses_with_usage'] += 1
            self.usage['input_tokens'] += usage.input_tokens
            self.usage['output_tokens'] += usage.output_tokens

    def report(self, status, metadata):
        elapsed = self.clock() - self.start_wall
        return {'status': status, 'mode': 'alibaba' if self.client else 'watch_only',
                'goal': self.goal, 'source': metadata or {}, 'attention_config': asdict(self.controller.config),
                'media_seconds_observed': self.last_observed, 'wall_seconds': elapsed,
                'sampled_observations': self.total_observations,
                'watch_observations_per_wall_second': self.total_observations / elapsed if elapsed else 0,
                'realtime_pacing': self.realtime,
                'watch_wall_seconds': self.watch_wall_seconds,
                'watch_media_seconds_per_wall_second': self.last_observed / self.watch_wall_seconds if self.watch_wall_seconds else None,
                'max_processing_lag_seconds': self.max_processing_lag_seconds,
                'peak_ring_jpeg_bytes': self.peak_ring_bytes, 'ring_evictions': self.ring.dropped_frames,
                'retained_seconds': (self.ring.newest_time - self.ring.oldest_time) if len(self.ring) else 0,
                'configured_ring_seconds': self.ring.duration_seconds,
                'configured_ring_jpeg_bytes': self.ring.max_bytes,
                'buffer_kind': 'all_frames_jpeg' if self.buffered_frames else 'sampled_jpeg_sequence',
                'buffered_source_frames': self.buffered_frames,
                'usage': self.usage, 'model_latencies_seconds': self.latencies,
                'estimated_cost_usd': sum(self.cost_estimates) if self.client and not self.unknown_cost_responses and self.cost_estimates else None,
                'known_partial_cost_usd': sum(self.cost_estimates) if self.cost_estimates else None,
                'responses_without_cost_estimate': self.unknown_cost_responses,
                'controller': self.controller.summary(),
                'limits': ['Watch features and generic tracks are not event recognition.',
                           'The ring retains up to the age limit or byte limit, whichever is reached first.',
                           'Model evidence uses selected frames and may omit brief action details.',
                           'Notifications are saved locally, not delivered via APNs.',
                           'No recall, false-alarm rate, or savings claim without labeled controls and a baseline.',
                           'Watch-only mode does not execute semantic analysis or prove model capability.']}

    def write_html(self, summary):
        rows = []
        for event in self.events:
            timestamp = event.get('at', event.get('available_at', event.get('observed_until', 0)))
            detail = {k: v for k, v in event.items() if k not in ('type', 'at', 'available_at')}
            rows.append(f'<tr><td>{timestamp:.2f}s</td><td>{html.escape(event["type"])}</td>'
                        f'<td><pre>{html.escape(json.dumps(detail, indent=2))}</pre></td></tr>')
        title = 'Vision · ' + ('Watch only — semantic models not run' if not self.client else 'Causal replay')
        page = '<!doctype html><meta charset="utf-8"><title>Vision replay</title><style>'
        page += 'body{font:15px system-ui;background:#111418;color:#edf1f5;margin:40px auto;max-width:1100px;padding:0 24px}h1{font-size:26px}table{width:100%;border-collapse:collapse}td,th{padding:12px;border-bottom:1px solid #303840;text-align:left;vertical-align:top}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}p{line-height:1.6;color:#bbc6d0}summary{cursor:pointer}</style>'
        page += f'<h1>{html.escape(title)}</h1><p>{html.escape(self.goal)}</p>'
        page += f'<p>{self.total_observations} observations · {self.last_observed:.2f}s observed · {self.peak_ring_bytes:,} peak JPEG bytes buffered</p>'
        page += '<p>Candidate changes are reasons to inspect. They do not establish an event or a safe scene. Model notifications are a local outbox; phone delivery is not connected.</p>'
        page += '<details><summary>Run metadata and limits</summary><pre>' + html.escape(json.dumps(summary, indent=2)) + '</pre></details>'
        page += '<h2>Observation and decision timeline</h2><table><tr><th>Time</th><th>Stage</th><th>Evidence</th></tr>' + ''.join(rows) + '</table>'
        (self.output / 'report.html').write_text(page)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('video', type=Path)
    parser.add_argument('--goal', default='Describe meaningful visible changes and interactions. Notify only when the evidence supports an event that needs attention; ordinary activity is valid.')
    parser.add_argument('--backend', choices=('watch', 'alibaba'), default='watch')
    parser.add_argument('--ask-key', action='store_true', help='Secure terminal prompt; never put the API key in a command argument')
    parser.add_argument('--output', type=Path, default=None, help='New output directory; existing runs are never overwritten')
    parser.add_argument('--model-cache', type=Path, default=Path('local/models'))
    parser.add_argument('--encoder', choices=('mobilenet', 'appearance'), default='mobilenet')
    parser.add_argument('--detector', choices=('ssdlite', 'none'), default='ssdlite')
    parser.add_argument('--sample-fps', type=float, default=5.0)
    parser.add_argument('--attend-interval', type=float, default=20.0)
    parser.add_argument('--inspect-interval', type=float, default=60.0)
    parser.add_argument('--max-calls', type=int, default=30)
    parser.add_argument('--attend-model', default='qwen3-vl-flash')
    parser.add_argument('--inspect-model', default='qwen3-vl-plus')
    parser.add_argument('--endpoint', default=ModelConfig().endpoint)
    parser.add_argument('--attend-input-usd-per-million', type=float)
    parser.add_argument('--attend-output-usd-per-million', type=float)
    parser.add_argument('--inspect-input-usd-per-million', type=float)
    parser.add_argument('--inspect-output-usd-per-million', type=float)
    parser.add_argument('--realtime', action='store_true', help='Also pace Watch-only replay; Alibaba is always paced')
    args = parser.parse_args(argv)
    if not args.video.is_file():
        parser.error('Choose a local video file for the replay experiment')
    if args.ask_key and args.backend != 'alibaba':
        parser.error('--ask-key requires --backend alibaba')
    if args.ask_key:
        os.environ['ALIBABA_API_KEY'] = getpass.getpass('Alibaba API key (not saved): ')
    if args.backend == 'alibaba' and not os.environ.get('ALIBABA_API_KEY', '').strip():
        parser.error('Use --ask-key or set ALIBABA_API_KEY in your terminal; never paste it into chat')
    config = AttentionConfig(attend_interval=args.attend_interval, inspect_interval=args.inspect_interval,
                             max_calls=args.max_calls)
    prices = {}
    for stage in ('attend', 'inspect'):
        input_rate, output_rate = (getattr(args, f'{stage}_{kind}_usd_per_million') for kind in ('input', 'output'))
        if (input_rate is None) != (output_rate is None):
            parser.error(f'Provide both input and output rates for {stage}')
        prices[f'{stage}_prices'] = None if input_rate is None else ModelPrices(input_rate, output_rate)
    client = None if args.backend == 'watch' else AlibabaVisionClient(ModelConfig(
        endpoint=args.endpoint, attend_model=args.attend_model, inspect_model=args.inspect_model, **prices))
    output = args.output or Path('local/replays') / time.strftime('%Y%m%d-%H%M%S')
    experiment = ReplayExperiment(args.goal, output, client=client, config=config,
                                  realtime=args.realtime or client is not None)
    source = VideoSource(args.video, sample_fps=args.sample_fps, encoder=args.encoder,
                         detector=args.detector, model_cache=args.model_cache,
                         on_frame=experiment.buffer_frame)
    summary = experiment.run(source, metadata=asdict(source.metadata))
    print(json.dumps({'status': summary['status'], 'mode': summary['mode'],
                      'report': str((output / 'report.html').resolve()),
                      'observations': summary['sampled_observations'], 'usage': summary['usage']}, indent=2))
    return 0 if summary['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
