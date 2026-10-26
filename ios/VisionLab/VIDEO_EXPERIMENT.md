# Monitor video experiment

Vision Lab has one **Monitor** tab. Write what to watch for or analyze in the text box, tap **Upload video**, and select a video from Photos or Files. Selection prepares and submits the full clip automatically. The reply appears below the controls and remains available when the app is reopened. The camera, explanatory footer, separate Describe video tab, preview and notification test controls are removed from this flow.

## Connection

Connection contains a **Model** picker and the masked **API key** field, plus Save and Close. Available choices are:

- `qwen3-vl-plus` (the existing default)
- `qwen3-vl-32b-instruct`
- `qwen3-vl-8b-instruct`

The same Singapore Alibaba endpoint and existing Lab Keychain entry are retained. Changing the picker changes the next request; a request already submitted keeps its original model and prompt. Access, quota and billing are specific to the selected model and the account. The interface does not inspect your quota automatically. Existing saved model names outside this list remain selectable so updating the app does not silently change the user's connection.

The endpoint defaults to `https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions`. A previously saved endpoint remains in use. Model selection and prompt use the existing UserDefaults keys; the API key retains service `com.vision.lab` and account `model-api-key`. Key updates use SecItemUpdate so an unsuccessful update does not first delete the existing secret. No authentication key is serialized into analysis metadata.

## Upload and background response

- The entire video is exported as compatible MP4 at up to 720p. The editable prompt is included in the model request. Requests ask for visible evidence and uncertainty without presuming the requested event occurs.
- A native `video_url` data URL carries the complete MP4. JSON is built incrementally on disk to avoid several in-memory video copies. Alibaba preparation targets 7 MB, below its 10 MB base64-string limit. Qwen3-VL-Plus accepts clips from 2 seconds to 1 hour; the 32B and 8B Instruct choices use the documented 20-minute limit. Server sampling and context still affect what is observed.
- Preparation is local and uses a finite iOS background-task allowance. The model's analysis is remote. Once handed to the file-backed background URLSession, upload and response delivery are managed by iOS while the app is suspended.
- The background session has a stable identifier, an application-delegate reconnection handler, and durable request/result metadata under Application Support/VideoAnalysis. The response is saved before completion is reported. Results and errors are restored on reopening. Request and temporary response payloads are cleaned up after completion. API keys stay in Keychain and the authenticated system-managed request.
- Switching apps or locking the phone does not explicitly cancel the analysis. The app does not disable auto-lock or display a requirement to keep it open. iOS controls background scheduling.
- **Force-quitting from the app switcher is different:** Apple cancels background URLSession transfers and does not automatically relaunch the app. This direct synchronous Alibaba integration cannot recover a lost response after force-quit. The app reports an interrupted transfer when reopened. Reliable force-quit recovery would require a durable server-side job/result API in front of Alibaba.
- Existing APNs registration callbacks and entitlements are preserved without their setup/test sections in Connection. This change does not establish an Alibaba-to-APNs completion service.

References: [Alibaba video input](https://www.alibabacloud.com/help/en/model-studio/vision), [Singapore model pricing and availability](https://www.alibabacloud.com/help/en/model-studio/model-pricing), [Apple background transfers](https://developer.apple.com/documentation/foundation/downloading-files-in-the-background), [background configuration and force-quit](https://developer.apple.com/documentation/foundation/urlsessionconfiguration/background(withidentifier:)).

## Validation

Run `zsh tools/check-video-wiring.sh`. The checks cover full native-video bytes, the editable prompt, JSON escaping, size and duration validation, response decoding, HTTP failures, authorization headers, persistent result restoration, quota errors, overlap prevention, cancellation and exclusion of credentials from saved analysis metadata. Network tests use controlled fixtures; they do not validate model understanding or physical-device background scheduling.

Signed iPhone and simulator builds validate the app. Simulator inspection validates the minimal screens and model choices separately. A real Alibaba inference response and a locked-phone end-to-end acceptance test remain separate observations.

```sh
xcodebuild -project ios/VisionLab.xcodeproj -scheme VisionLab \
  -destination 'generic/platform=iOS' \
  -derivedDataPath /private/tmp/vision-monitor-build build
```
