# Vision Live camera

Vision Lab 0.3.0 (3) adds a native **Live** tab. It keeps the existing uploaded-video
Monitor tab. The Mac connects to Tapo's RTSP stream; the iPhone pairs to the Mac
over the same trusted LAN. No browser is needed to use the camera interface.

## Start the Mac bridge

From this repository, with the existing stream dependencies installed:

```sh
.venv/bin/python -m vision.camera_gateway --host CAMERA_IP --bind MAC_LAN_IP --ask-key
```

Use the camera's current IP and this Mac's LAN IP. Enter the dedicated Tapo
Camera Account and Alibaba key in the terminal; they are not saved. The bridge
uses port 8765, a five-observation/second sampled preview, and a one-hour capture
limit by default. Restart it for another session. Keep the Mac awake.

On the iPhone, open **Vision Lab → Live → Pair Mac**. Enter
`http://MAC_LAN_IP:8765` and the token in `local/gateway/pairing-token`. You can
read/copy that file locally; do not send it through chat or commit it. Each bridge
launch generates a fresh token. Pairing lives in iPhone Keychain. Only the bridge
token goes to the Mac from this screen; camera and Alibaba credentials stay on
the Mac. Local HTTP is not encrypted: use trusted Wi-Fi and never forward this
port to the internet. The bridge defaults to loopback unless `--bind` is supplied.

## See and control Qwen

1. Confirm **Camera: streaming** and a moving preview.
2. Enter a concrete standing goal, such as “Notify if someone enters through the
   doorway. Describe what is visible and distinguish uncertainty.”
3. Select a vision model available to your Alibaba key. Plus is the initial
   default; it does not silently fall back to another model.
4. Choose a request cap and tap **Start monitoring**. Selected images are sent to
   Alibaba. Stop monitoring before applying a different goal/model.

**Watching** means the local scheduler is observing. **Analyzing** means a model
request is pending. A moving preview alone does not prove Qwen is responding.
Each completed interpretation shows its evidence, model, latency and observation
cutoff. The **Sent to Qwen** strip contains the exact images/timestamps submitted
in recent requests, rather than a substitute preview. Some intermediate frames
are omitted; the neural model reasons over the supplied chronological images.
HTTP configuration failures surface as errors and stop monitoring. Reaching the
request cap is visibly marked, and it does not imply the scene is safe.

The scheduler uses motion/appearance changes, periodic reviews and semantic
follow-up. Only the deeper inspection stage can produce a detection alert.
The goal, recent visual context and model evidence matter; a motion spike alone
does not trigger a semantic notification. Notifications have a cooldown.

Changing the goal starts fresh model context. A stop prevents new calls and
suppresses alerts from the stopped session, but already submitted inference may
still finish and use quota. Stop waits for those calls before another session.
Disconnecting the preview or leaving the iPhone screen does not stop the Mac.

For the math, read `stream_video.py`'s changed-pixel fraction, then
`stream_attention.py`'s thresholds and scheduling, followed by
`camera_gateway.py`'s evidence and result handling. Predict a light switch versus
a person walking through the doorway; compare the signal with Qwen's evidence.

## Notifications

Tap **Enable detection alerts** on the phone and allow notifications. While Vision
is open and receiving fresh detections, it can display local notifications if
remote push is unavailable. The detection history remains visible without
notification permission. No local polling runs while the app is suspended.

For notifications while the phone is locked or Vision is closed, the Mac sends
real model detections to APNs. Compile the sender once:

```sh
mkdir -p local/gateway
swiftc -parse-as-library tools/send_push.swift -o local/gateway/send_push
local/gateway/send_push --self-test
local/gateway/send_push --check-config "$PWD/local/push-config.json"
```

The ignored config needs `keyFile` (absolute path to your Apple APNs `.p8` key),
`keyID`, and `teamID`. The prior `registrationFile` is optional for detection
delivery. Never commit these credentials. If the existing config points to a
missing key, restore that file locally or update its local path. The UI reports
push as unconfigured when the key file is missing. Creating/replacing an Apple
key is a separate account step; no key is generated or revoked by this feature.

The app sends its current notification registration directly to the paired Mac.
Release/TestFlight builds use production APNs; debug builds use sandbox. APNs
authentication and the detection payload travel to the helper over stdin, not
process arguments. The UI distinguishes `accepted by APNs`, `failed`,
`not configured` and a full delivery queue. Apple acceptance is not proof of a
visible banner; verify delivery on the actual locked phone.

## Data and checks

Raw preview and submitted image strips are bounded in memory. Model descriptions
and run reports stay under ignored `local/gateway/runs/`. No camera video,
credentials or phone registration is committed. All image/state/control routes
require pairing; cross-origin browser control is rejected and HTTP concurrency
is bounded. Notifications use a bounded delivery queue.

```sh
.venv/bin/python -m unittest discover -s vision -p 'test_*.py'
zsh tools/check-video-wiring.sh
```

Fixture tests cover authenticated preview/control, stale frames, submitted
evidence, usage, semantic escalation, stop behavior and notification outcomes.
They do not establish real Qwen accuracy or real-device notification delivery.

## Release and installation

The release candidate is **Sutskever / Vision Lab, com.vision.lab, 0.3.0 (3)**.
Use the release handoff for its current archive/upload state. It is not a
TestFlight installation until Apple has processed the uploaded build, assigned
testers can see it, and installation completes from the TestFlight app.

If Vision is absent from TestFlight, the user checks **App Store Connect →
Sutskever → TestFlight → Internal Testing → Vision Lab**, adds/confirms the tester,
and resends the invitation if needed. Accept the invitation on the iPhone with
the intended Apple account. Check automatic distribution before uploading, then
manually select the processed build for testing. The browser remains user-operated.

References: [Apple TestFlight installation](https://testflight.apple.com/),
[internal testers](https://developer.apple.com/help/app-store-connect/test-a-beta-version/add-internal-testers/).
