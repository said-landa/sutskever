# Tapo: first live experiment

This experiment runs on the Mac. The iPhone app still has its existing video
upload flow. No iOS build is needed for this Python change.

## Connect your camera

1. Find the exact Tapo model. Most wired cameras support RTSP; many battery
   models do not. Keep this Mac and the camera on the same local network.
2. In Tapo, open the camera → Settings → Advanced Settings → Camera Account.
   Create its dedicated username/password, separate from your TP-Link cloud login.
3. Find the local IP in Settings → Device Info. Keep credentials out of chat.

Start with the standard-quality stream, a 60-second limit, and no cloud inference:

```sh
.venv/bin/python -m vision.stream_live --host 192.168.1.50
```

Replace the example IP. The terminal prompts for the camera account; password
entry is hidden. The optional environment variables `TAPO_CAMERA_USERNAME` and
`TAPO_CAMERA_PASSWORD` are also supported. Never place a credential-bearing RTSP
URL in command arguments. The connection uses RTSP over TCP on port 554.

Dependencies and pretrained local model weights, if not already installed:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[stream]'
.venv/bin/python tools/prepare_stream_models.py
```

For a simpler local comparison without pretrained models, use
`--encoder appearance --detector none`. That is a motion/appearance baseline;
it does not identify events.

Then enable automatic Qwen reviews:

```sh
.venv/bin/python -m vision.stream_live --host 192.168.1.50 \
  --backend alibaba --ask-key --seconds 60 --max-calls 8
```

The API key is prompted locally. This sends selected camera JPEGs, their times,
and bounded scene context to Alibaba's Singapore endpoint. It uses the existing
`qwen3-vl-flash` attention model and `qwen3-vl-plus` inspection model. It does not
require a new user prompt for each scene. A standing observation goal supplies
the scope; `--goal` lets you change it.

The scheduler checks periodically (attention every 20 seconds, deeper inspection
every 30 seconds by default), reacts to change signals, and follows up on model
uncertainty. Calls can happen more frequently when the scene changes, subject
to the global call cap, stage cooldowns, and one in-flight call per stage.
Reaching the call cap can leave an unresolved scene; the report marks that
limitation. Failed calls are not automatically retried by the HTTP client.
HTTP 400/401/403/404 stops the run after the first observed failure. A new camera
frame cannot repair a model name, request schema, account permission, key or
endpoint problem. Already submitted calls can still finish during shutdown.

### If Qwen returns HTTP 404

The error log now includes the model, HTTP status, and a recognized provider code
when present. Arbitrary provider messages are discarded so echoed credentials or
camera details never enter the logs. A 404 alone does not establish whether the
model is unavailable, account access is missing, or the endpoint is wrong.

If your earlier uploaded-video test worked with `qwen3-vl-plus`, isolate the live
connection with the same key and a single request using Plus:

```sh
.venv/bin/python -m vision.stream_live --host 192.168.1.50 \
  --backend alibaba --ask-key --seconds 15 --max-calls 1 \
  --attend-model qwen3-vl-plus --inspect-model qwen3-vl-plus
```

This deliberately changes both stages from the Flash/Plus comparison; it is a
connection test, not a measurement of Flash performance. With one call, final
follow-up can be denied by the cap and the run may be marked degraded even when
that one inference succeeds. Look for a `result` with actual usage.

Use `--endpoint` to supply a full HTTPS `.../chat/completions` URL if your key uses
a different region or workspace. Do not change region by guessing: match the
endpoint to the Alibaba Model Studio console or your previously working app.
The original Singapore endpoint remains the default. Custom endpoints receive
the API key and selected images; only use an endpoint you trust.

See [Alibaba error codes](https://www.alibabacloud.com/help/en/model-studio/error-code)
and [regional vision endpoint guidance](https://help.aliyun.com/en/model-studio/qwen-vl-compatible-with-openai).

The camera capture stops at the duration limit; decoder reads can take up to
the configured five-second timeout. Already submitted model work may finish
after capture stops and can trigger a bounded final review. Ctrl-C interrupts
observation; an already running HTTP request may finish before Python exits.
Disconnects fail the run rather than reporting a quiet or completed scene.

## See what it is doing

The terminal streams observations, request reasons, decisions, and usage. Each
run creates a new folder under ignored `local/camera/` containing:

- `events.jsonl`: observation/decision timeline, including request frame times.
- `summary.json`: reported tokens, call count, model latency, local CPU time,
  process peak RSS, overwritten frames, and JPEG bytes submitted.
- `report.html`: a local human-readable view of that run.
- `notifications.jsonl`, if produced: a local outbox; phone push is not connected.

Images stay in bounded RAM buffers; raw camera video is not recorded. Model
descriptions can still be personal, so the entire `local/` directory stays out
of Git. JPEG bytes exclude base64, JSON, HTTP, and incoming RTSP bandwidth.
Token usage comes from provider responses; unavailable usage stays unknown.
CPU/RSS describe this Mac process, not Qwen GPU usage or energy consumption.
Peak RSS is for the process lifetime, not just the capture interval.

No dollar rates are assumed. Supply both input/output USD rates for each stage
using `--attend-input-usd-per-million`, `--attend-output-usd-per-million`,
`--inspect-input-usd-per-million`, and `--inspect-output-usd-per-million` if you
want an estimate based on rates verified for your account. A failed/missing
response can make the full cost unknown; partial known cost is separate.

## Learn it by changing one thing

Read `vision/stream_video.py`, `vision/stream_attention.py`, and then
`vision/stream_live.py`. The local Watch code currently contains these equations:

```text
changed_fraction = count(abs(gray_now - gray_previous) > 20) / pixel_count
motion = max(changed_fraction, foreground_fraction)
embedding_change = 1 - dot(normalized_embedding_now, normalized_embedding_previous)
cost_USD = (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
```

The gray images are blurred first. Foreground fraction uses a background model,
and change peaks between emitted observations are retained. The neural encoder
also compares against a slowly adapting baseline and scales/clamps its signal.
These are attention heuristics, not probabilities that an event occurred.

For a small arithmetic check: 2,400 changed pixels out of 30,000 gives `0.08`,
the default motion threshold in `AttentionConfig`. Predict what happens when a
lamp switches on versus when a person moves. Run each test and compare the motion
value, request reason, and Qwen's visible evidence.

At `--sample-fps 2`, the local learned models run at up to two observations per
second. This does **not** send two cloud requests per second. Each attention
request includes up to six images; inspection includes up to sixteen. Repeat
with `--sample-fps 5`, performing the same action. Compare missed short actions,
CPU time, submitted images, and tokens. Higher local sampling need not increase
cloud tokens because the scheduler and evidence limits are separate controls.

The decoder continuously drains the stream into a one-frame mailbox. Slow
consumers overwrite older frames, and the report counts them. The rolling image
buffer retains consumed frames, not every camera frame. Local arrival timestamps
keep observation causal but cannot measure camera-side/network buffering or true
sensor-to-decision latency. The model sees selected chronological images and can
reason across them; it does not interpret each frame independently in isolation.

For the first real test, log the times of a quiet interval, a walk across view,
a moved object, and a light change. Compare those observations against decisions
and arrival times. Automated fixture tests verify plumbing, not recognition
accuracy, false alarms, or real-camera performance.

```sh
.venv/bin/python -m unittest discover -s vision -p 'test_stream*.py'
```

References: [Tapo RTSP setup and supported-device limits](https://www.tapo.com/en/faqs/how-to-view-tapo-camera-on-pc-nas-or-nvr-using-rtsponvif/),
[Alibaba visual understanding](https://www.alibabacloud.com/help/en/model-studio/vision),
[Alibaba model pricing](https://www.alibabacloud.com/help/en/model-studio/model-pricing).
