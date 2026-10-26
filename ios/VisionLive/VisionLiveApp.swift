import SwiftUI
import AVFoundation
import UserNotifications
import UniformTypeIdentifiers
import Security

@main
struct VisionLiveApp: App {
    @StateObject private var monitor = Monitor()
    var body: some Scene {
        WindowGroup { MonitorView(monitor: monitor) }
    }
}

struct Frame {
    let data: Data
    let captured: Date
    let time: Double
}

struct Decision: Decodable {
    let status: String
    let evidence: String
}

struct EventRecord: Identifiable {
    let id = UUID()
    let date: Date
    let evidence: String
    let latency: Double
}

final class Notifications: NSObject, UNUserNotificationCenterDelegate {
    override init() {
        super.init()
        UNUserNotificationCenter.current().delegate = self
    }
    func enable() async throws -> Bool {
        try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])
    }
    func send(title: String, body: String) async throws {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        try await UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound, .list])
    }
}

enum KeyStore {
    static let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "com.vision.live", kSecAttrAccount as String: "model-api-key"]
    static func read() -> String {
        var q = query
        q[kSecReturnData as String] = true
        var result: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return "" }
        return String(data: data, encoding: .utf8) ?? ""
    }
    static func save(_ value: String) throws {
        SecItemDelete(query as CFDictionary)
        guard !value.isEmpty else { return }
        var q = query
        q[kSecValueData as String] = Data(value.utf8)
        q[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        let status = SecItemAdd(q as CFDictionary, nil)
        guard status == errSecSuccess else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(status)) }
    }
}

// Capture state is confined to queue; onFrame is assigned once before capture starts.
final class Camera: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate, @unchecked Sendable {
    let session = AVCaptureSession()
    private let queue = DispatchQueue(label: "vision.camera")
    private let context = CIContext()
    private var last = Date.distantPast
    var onFrame: ((Data) -> Void)?
    func start() async throws {
        guard await AVCaptureDevice.requestAccess(for: .video) else {
            throw NSError(domain: "Vision", code: 1, userInfo: [NSLocalizedDescriptionKey: "Allow camera access in Settings."])
        }
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            queue.async {
                do {
                    if self.session.inputs.isEmpty {
                        self.session.beginConfiguration()
                        self.session.sessionPreset = .medium
                        guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back) else {
                            self.session.commitConfiguration()
                            throw NSError(domain: "Vision", code: 2, userInfo: [NSLocalizedDescriptionKey: "No camera available."])
                        }
                        let input = try AVCaptureDeviceInput(device: device)
                        let output = AVCaptureVideoDataOutput()
                        output.alwaysDiscardsLateVideoFrames = true
                        guard self.session.canAddInput(input), self.session.canAddOutput(output) else {
                            self.session.commitConfiguration()
                            throw NSError(domain: "Vision", code: 3, userInfo: [NSLocalizedDescriptionKey: "Camera setup failed."])
                        }
                        self.session.addInput(input)
                        self.session.addOutput(output)
                        output.setSampleBufferDelegate(self, queue: self.queue)
                        if let connection = output.connection(with: .video), connection.isVideoRotationAngleSupported(90) {
                            connection.videoRotationAngle = 90
                        }
                        self.session.commitConfiguration()
                    }
                    self.last = .distantPast
                    self.session.startRunning()
                    continuation.resume()
                } catch { continuation.resume(throwing: error) }
            }
        }
    }
    func stop() { queue.async { self.session.stopRunning() } }
    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        let now = Date()
        guard now.timeIntervalSince(last) >= 0.5, let buffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        last = now
        let source = CIImage(cvPixelBuffer: buffer)
        let scale = min(1, 640 / max(source.extent.width, source.extent.height))
        let image = source.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        guard let cg = context.createCGImage(image, from: image.extent),
              let jpeg = UIImage(cgImage: cg).jpegData(compressionQuality: 0.65) else { return }
        onFrame?(jpeg)
    }
}

@MainActor
final class Monitor: ObservableObject {
    @Published var rule = UserDefaults.standard.string(forKey: "rule") ?? "An object falls off a table onto the floor. An object already on the floor does not count."
    @Published var endpoint = UserDefaults.standard.string(forKey: "endpoint") ?? ""
    @Published var model = UserDefaults.standard.string(forKey: "model") ?? ""
    @Published var apiKey = KeyStore.read()
    @Published var running = false
    @Published var status = "Choose an event and connect a vision model."
    @Published var evidence = ""
    @Published var events: [EventRecord] = []
    @Published var checks = 0
    @Published var latency: Double?
    @Published var replayImage: UIImage?
    @Published var isReplay = false
    let camera = Camera()
    private let notifications = Notifications()
    private var frames: [Frame] = []
    private var task: Task<Void, Never>?
    private var replayTask: Task<Void, Never>?
    private var startTime = Date()
    private var generation = UUID()
    private var latched = false
    private var absentCount = 0
    private var activeRule = ""
    private var activeEndpoint = ""
    private var activeModel = ""
    private var activeKey = ""
    private var lastCheckedFrame: Date?

    init() {
        camera.onFrame = { [weak self] data in
            Task { @MainActor in self?.append(data: data) }
        }
    }
    func save() throws {
        try KeyStore.save(apiKey)
        UserDefaults.standard.set(rule, forKey: "rule")
        UserDefaults.standard.set(endpoint, forKey: "endpoint")
        UserDefaults.standard.set(model, forKey: "model")
    }
    func testNotification() async {
        do {
            guard try await notifications.enable() else { status = "Notifications denied. Enable them in Settings."; return }
            try await notifications.send(title: "Vision · delivery test", body: "Notification delivery works. This is a manual test, not a model detection.")
            status = "Delivery test sent. This does not test the model."
        } catch { status = error.localizedDescription }
    }
    private func prepare() async throws {
        guard !rule.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, !model.isEmpty,
              let url = URL(string: endpoint), let host = url.host,
              url.scheme == "https" || (url.scheme == "http" &&
                (host == "localhost" || host.hasSuffix(".local") || host.hasPrefix("192.168.") || host.hasPrefix("10."))) else {
            throw NSError(domain: "Vision", code: 4, userInfo: [NSLocalizedDescriptionKey: "Enter an event, model name, and full HTTPS chat/completions URL (or a local-network HTTP URL)."])
        }
        try save()
        guard try await notifications.enable() else {
            throw NSError(domain: "Vision", code: 5, userInfo: [NSLocalizedDescriptionKey: "Enable notifications in Settings before monitoring."])
        }
        activeRule = rule; activeEndpoint = endpoint; activeModel = model; activeKey = apiKey
        frames = []; events = []; checks = 0; evidence = ""; latency = nil; lastCheckedFrame = nil
        latched = false; absentCount = 0; startTime = Date(); generation = UUID()
        running = true
        UIApplication.shared.isIdleTimerDisabled = true
    }
    func startCamera() async {
        do {
            try await prepare()
            isReplay = false; replayImage = nil
            try await camera.start()
            guard running else { camera.stop(); return }
            startChecks()
        } catch { stop(); status = error.localizedDescription }
    }
    func startVideo(_ url: URL) async {
        do {
            try await prepare()
            isReplay = true
            let asset = AVURLAsset(url: url)
            let duration = try await asset.load(.duration).seconds
            guard duration.isFinite, duration > 0 else { throw NSError(domain: "Vision", code: 6, userInfo: [NSLocalizedDescriptionKey: "Invalid video duration."]) }
            let generator = AVAssetImageGenerator(asset: asset)
            generator.appliesPreferredTrackTransform = true
            generator.maximumSize = CGSize(width: 640, height: 640)
            generator.requestedTimeToleranceBefore = .zero
            generator.requestedTimeToleranceAfter = .zero
            startChecks()
            let token = generation
            replayTask = Task {
                do {
                    for index in 0..<Int(ceil(duration * 2)) {
                        try Task.checkCancellation()
                        let time = Double(index) / 2
                        let due = startTime.addingTimeInterval(time).timeIntervalSinceNow
                        if due > 0 { try await Task.sleep(for: .seconds(due)) }
                        let result = try await generator.image(at: CMTime(seconds: time, preferredTimescale: 600))
                        guard running, generation == token else { return }
                        let image = UIImage(cgImage: result.image)
                        replayImage = image
                        if let data = image.jpegData(compressionQuality: 0.65) { append(data: data, time: time) }
                    }
                    // Leave the final frames available for the in-flight/final decision.
                    try await Task.sleep(for: .seconds(3))
                    while running && status == "Analyzing…" { try await Task.sleep(for: .seconds(0.25)) }
                    guard generation == token else { return }
                    let count = events.count
                    stop()
                    status = "Replay finished: \(count) detections. Zero detections does not prove the event never occurred."
                } catch is CancellationError {} catch { stop(); status = error.localizedDescription }
            }
        } catch { stop(); status = error.localizedDescription }
    }
    private func append(data: Data, time: Double? = nil) {
        guard running else { return }
        let now = Date()
        frames.append(Frame(data: data, captured: now, time: time ?? now.timeIntervalSince(startTime)))
        frames.removeAll { now.timeIntervalSince($0.captured) > 6 }
        if frames.count > 12 { frames.removeFirst(frames.count - 12) }
    }
    private func startChecks() {
        status = "Watching…"
        let token = generation
        task = Task {
            while !Task.isCancelled && running && generation == token {
                do {
                    try await Task.sleep(for: .seconds(2))
                    guard frames.count >= 2, frames.last?.captured != lastCheckedFrame else { continue }
                    let batch = frames
                    lastCheckedFrame = batch.last?.captured
                    status = "Analyzing…"
                    let started = Date()
                    let decision = try await analyze(batch)
                    guard !Task.isCancelled, running, generation == token else { return }
                    latency = Date().timeIntervalSince(started)
                    checks += 1; evidence = decision.evidence
                    let age = Date().timeIntervalSince(batch.last!.captured)
                    status = age > 8 ? "Delayed result · \(Int(age))s old" : "Watching · \(decision.status)"
                    if decision.status == "occurred" {
                        absentCount = 0
                        if !latched {
                            let event = EventRecord(date: Date(), evidence: decision.evidence, latency: age)
                            events.insert(event, at: 0)
                            if events.count > 100 { events.removeLast() }
                            try await notifications.send(title: "Vision · event detected", body: decision.evidence + " (latest frame \(Int(age))s ago)")
                            latched = true
                        }
                    } else if decision.status == "not_observed" {
                        absentCount += 1
                        if absentCount >= 2 { latched = false }
                    }
                } catch is CancellationError { return }
                catch {
                    guard generation == token else { return }
                    stop(); status = "Monitoring stopped: " + error.localizedDescription
                    return
                }
            }
        }
    }
    func stop() {
        generation = UUID(); running = false
        task?.cancel(); replayTask?.cancel(); camera.stop()
        UIApplication.shared.isIdleTimerDisabled = false
        status = "Stopped."
    }
    private func analyze(_ frames: [Frame]) async throws -> Decision {
        let prompt = """
        Monitor this chronological video prefix for the user's event: \(activeRule)
        Only use supplied frames. No future frames exist. An object already in its final state does not establish a transition.
        Return only JSON: {"status":"occurred|not_observed|uncertain","evidence":"brief visible evidence in English"}.
        occurred means the event is established by the visible sequence. not_observed means it is not observed in this window;
        it does not mean it never happened. Use uncertain when visibility or sampling is insufficient. Do not invent motion or intent.
        """
        var content: [[String: Any]] = []
        for frame in frames {
            content.append(["type": "text", "text": String(format: "Frame at %.2f seconds", frame.time)])
            content.append(["type": "image_url", "image_url": ["url": "data:image/jpeg;base64," + frame.data.base64EncodedString()]])
        }
        let payload: [String: Any] = ["model": activeModel, "messages": [
            ["role": "system", "content": prompt], ["role": "user", "content": content]],
            "max_tokens": 200, "temperature": 0]
        var request = URLRequest(url: URL(string: activeEndpoint)!)
        request.httpMethod = "POST"; request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if !activeKey.isEmpty { request.setValue("Bearer " + activeKey, forHTTPHeaderField: "Authorization") }
        request.httpBody = try JSONSerialization.data(withJSONObject: payload)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let code = (response as? HTTPURLResponse)?.statusCode ?? 0
            throw NSError(domain: "Vision", code: code, userInfo: [NSLocalizedDescriptionKey: "Model endpoint returned HTTP \(code). Check its URL, model name and key."])
        }
        guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let choices = root["choices"] as? [[String: Any]], let message = choices.first?["message"] as? [String: Any],
              let raw = message["content"] as? String else {
            throw NSError(domain: "Vision", code: 7, userInfo: [NSLocalizedDescriptionKey: "Endpoint must return an OpenAI-compatible chat completion."])
        }
        let clean = raw.replacingOccurrences(of: "```json", with: "").replacingOccurrences(of: "```", with: "").trimmingCharacters(in: .whitespacesAndNewlines)
        let decision = try JSONDecoder().decode(Decision.self, from: Data(clean.utf8))
        guard ["occurred", "not_observed", "uncertain"].contains(decision.status), !decision.evidence.isEmpty else {
            throw NSError(domain: "Vision", code: 8, userInfo: [NSLocalizedDescriptionKey: "Model returned an invalid decision."])
        }
        return decision
    }
}

final class PreviewUIView: UIView {
    override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
}
struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession
    func makeUIView(context: Context) -> PreviewUIView {
        let view = PreviewUIView()
        let layer = view.layer as! AVCaptureVideoPreviewLayer
        layer.session = session; layer.videoGravity = .resizeAspectFill
        return view
    }
    func updateUIView(_ view: PreviewUIView, context: Context) {}
}

struct MonitorView: View {
    @ObservedObject var monitor: Monitor
    @Environment(\.scenePhase) private var scenePhase
    @State private var settings = false
    @State private var importing = false
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Text("Define one event. Watch it happen. Get an alert.").font(.title2.bold())
                    Group {
                        if monitor.isReplay, let image = monitor.replayImage {
                            Image(uiImage: image).resizable().scaledToFit()
                        } else { CameraPreview(session: monitor.camera.session) }
                    }.frame(height: 260).background(.black).clipShape(RoundedRectangle(cornerRadius: 20))
                    Text("Notify me when…").font(.headline)
                    TextEditor(text: $monitor.rule).frame(height: 85).padding(8)
                        .background(.thinMaterial).clipShape(RoundedRectangle(cornerRadius: 12)).disabled(monitor.running)
                    if monitor.running {
                        Button("Stop monitoring", role: .destructive) { monitor.stop() }.buttonStyle(.borderedProminent)
                    } else {
                        HStack {
                            Button("Live camera", systemImage: "video") { Task { await monitor.startCamera() } }
                            Button("Replay video", systemImage: "film") { importing = true }
                        }.buttonStyle(.borderedProminent)
                    }
                    Text(monitor.status).font(.headline).accessibilityIdentifier("monitorStatus")
                    if let latency = monitor.latency {
                        Text(String(format: "%d checks · last model response %.1fs", monitor.checks, latency)).font(.caption).foregroundStyle(.secondary)
                    }
                    if !monitor.evidence.isEmpty { Text(monitor.evidence) }
                    Text("Keep this app open while monitoring. Video frames go to your configured model endpoint. Sampling is 2 frames/s; brief events may be missed.")
                        .font(.caption).foregroundStyle(.secondary)
                    if !monitor.events.isEmpty {
                        Text("Detections this session").font(.headline)
                        ForEach(monitor.events) { event in
                            VStack(alignment: .leading, spacing: 4) {
                                Text(event.evidence)
                                Text(event.date, style: .time).font(.caption).foregroundStyle(.secondary)
                            }.padding().frame(maxWidth: .infinity, alignment: .leading).background(.thinMaterial).clipShape(RoundedRectangle(cornerRadius: 12))
                        }
                    }
                }.padding()
            }.navigationTitle("Vision")
                .toolbar { Button("Connection", systemImage: "gear") { settings = true }.disabled(monitor.running) }
                .sheet(isPresented: $settings) {
                    NavigationStack {
                        Form {
                            Section("Vision model connection") {
                                TextField("Full chat/completions URL", text: $monitor.endpoint).textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                                TextField("Model name", text: $monitor.model).textInputAutocapitalization(.never).autocorrectionDisabled()
                                SecureField("API key (if required)", text: $monitor.apiKey)
                                Text("Use a vision-capable, OpenAI-compatible endpoint. Your key is stored in the iPhone Keychain.").font(.caption)
                            }
                            Section("Notification delivery") {
                                Button("Send a labeled test notification") { Task { await monitor.testNotification() } }
                                Text("This verifies notification delivery only. Model detections are shown separately.").font(.caption)
                            }
                        }.navigationTitle("Connection").toolbar {
                            Button("Save") {
                                do { try monitor.save(); settings = false }
                                catch { monitor.status = error.localizedDescription }
                            }
                        }
                    }
                }
                .fileImporter(isPresented: $importing, allowedContentTypes: [.movie]) { result in
                    do {
                        let source = try result.get()
                        let access = source.startAccessingSecurityScopedResource()
                        defer { if access { source.stopAccessingSecurityScopedResource() } }
                        let copy = FileManager.default.temporaryDirectory.appendingPathComponent("replay." + source.pathExtension)
                        try? FileManager.default.removeItem(at: copy)
                        try FileManager.default.copyItem(at: source, to: copy)
                        Task { await monitor.startVideo(copy) }
                    } catch { monitor.status = error.localizedDescription }
                }
                .onChange(of: scenePhase) { _, phase in
                    if phase != .active && monitor.running { monitor.stop(); monitor.status = "Paused because the app left the foreground." }
                }
        }
    }
}
