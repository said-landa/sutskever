import SwiftUI
import Security
import UserNotifications

struct LiveState: Decodable {
    struct Camera: Decodable { let status: String; let frame_age_seconds: Double?; let sequence: Int }
    struct Monitor: Decodable {
        let phase: String; let goal: String; let run_id: String; let model: String
        let pending: [Int]; let calls: Int; let input_tokens: Int; let output_tokens: Int; let max_calls: Int
    }
    struct Result: Decodable, Identifiable {
        let id: Int; let type: String; let stage: String; let model: String?
        let action: String?; let evidence: String?; let event_summary: String?
        let latency_seconds: Double?; let observed_until: Double?; let available_at: Double?
        let message: String?; let provider_code: String?; let received_at: Double
    }
    struct Alert: Decodable, Identifiable {
        let id: String; let summary: String; let evidence: String; let received_at: Double; let delivery: String
    }
    struct Evidence: Decodable { let request_id: String; let times: [Double] }
    let camera: Camera; let monitor: Monitor; let results: [Result]; let alerts: [Alert]
    let evidence: [Evidence]; let error: String?; let push_configured: Bool; let push_registered: Bool
}

enum LiveConnectionError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let text) = self { return text }; return nil }
}

struct LiveConnection {
    let base: URL
    let token: String
    init(address: String, token: String) throws {
        guard let parts = URLComponents(string: address.trimmingCharacters(in: .whitespacesAndNewlines)),
              let url = parts.url, let host = parts.host, ["http", "https"].contains(parts.scheme ?? ""),
              parts.user == nil, parts.password == nil, parts.query == nil, parts.fragment == nil,
              parts.path.isEmpty || parts.path == "/" else {
            throw LiveConnectionError.message("Enter the Mac bridge address, such as http://your-mac.local:8765.")
        }
        let octets = host.split(separator: ".").compactMap { Int($0) }
        let privateIP = octets.count == 4 && octets.allSatisfy { (0...255).contains($0) } &&
            (octets[0] == 10 || octets[0] == 127 || (octets[0] == 192 && octets[1] == 168) ||
             (octets[0] == 172 && (16...31).contains(octets[1])))
        guard parts.scheme == "https" || privateIP || host == "localhost" || host.hasSuffix(".local") else {
            throw LiveConnectionError.message("Use a trusted local Mac address for HTTP.")
        }
        let clean = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard (32...128).contains(clean.count), clean.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-" || $0 == "_") }) else {
            throw LiveConnectionError.message("Copy the pairing token from the Mac's local/gateway/pairing-token file.")
        }
        base = url; self.token = clean
    }
    func request(_ path: String, body: [String: Any]? = nil) throws -> URLRequest {
        var request = URLRequest(url: base.appendingPathComponent(path), cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 8)
        request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        if let body {
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.setValue("1", forHTTPHeaderField: "X-Vision-Client")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        return request
    }
}

// This session never forwards a pairing credential to a redirect or persists a
// camera frame in URLCache. The bridge only exposes bounded JPEGs/JSON.
final class LiveHTTPDelegate: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}

@MainActor
final class LiveCameraModel: ObservableObject {
    @Published var state: LiveState?
    @Published var image: UIImage?
    @Published var evidenceImages: [UIImage] = []
    @Published var evidenceTimes: [Double] = []
    @Published var error: String?
    @Published var connected = false
    @Published var busy = false
    @Published var address = UserDefaults.standard.string(forKey: "liveBridgeAddress") ?? ""
    @Published var pairingToken = ""
    @Published var goal = UserDefaults.standard.string(forKey: "liveGoal") ?? "Notify me if someone enters the room. Describe the visible evidence and uncertainty."
    @Published var model = UserDefaults.standard.string(forKey: "liveModel") ?? "qwen3-vl-plus"
    @Published var maxCalls = 30
    private var connection: LiveConnection?
    private var pollTask: Task<Void, Never>?
    private var evidenceID = ""
    private var lastSequence = -1
    private var registrationSent = ""
    private var generation = UUID()
    private var seenAlerts = UserDefaults.standard.stringArray(forKey: "liveSeenAlerts") ?? []
    private let delegate = LiveHTTPDelegate()
    private lazy var session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.urlCache = nil; config.httpCookieStorage = nil
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: config, delegate: delegate, delegateQueue: nil)
    }()
    init() { pairingToken = Self.readPairingToken() }
    static var pairingQuery: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "com.vision.lab",
         kSecAttrAccount as String: "camera-bridge-pairing"]
    }
    static func readPairingToken() -> String {
        var query = pairingQuery; query[kSecReturnData as String] = true
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess, let data = result as? Data else { return "" }
        return String(decoding: data, as: UTF8.self)
    }
    private func savePairingToken() throws {
        let data = Data(pairingToken.trimmingCharacters(in: .whitespacesAndNewlines).utf8)
        let updates: [String: Any] = [kSecValueData as String: data,
                                      kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
        let status = SecItemUpdate(Self.pairingQuery as CFDictionary, updates as CFDictionary)
        if status == errSecItemNotFound {
            var query = Self.pairingQuery; updates.forEach { query[$0.key] = $0.value }
            let added = SecItemAdd(query as CFDictionary, nil)
            guard added == errSecSuccess else { throw LiveConnectionError.message("Could not save pairing in Keychain.") }
        } else if status != errSecSuccess { throw LiveConnectionError.message("Could not save pairing in Keychain.") }
    }
    private func fetch(_ path: String, connection: LiveConnection, body: [String: Any]? = nil) async throws -> Data {
        let (data, response) = try await session.data(for: connection.request(path, body: body))
        guard let http = response as? HTTPURLResponse else { throw LiveConnectionError.message("Bridge did not return an HTTP response.") }
        guard http.statusCode == 200 else {
            throw LiveConnectionError.message(http.statusCode == 401 ? "Pairing expired. Copy the current Mac pairing token." :
                "Bridge HTTP \(http.statusCode). Check the camera or wait for pending analysis before restarting.")
        }
        guard data.count <= 2_000_000 else { throw LiveConnectionError.message("Bridge response was too large.") }
        return data
    }
    func connect() {
        do {
            let next = try LiveConnection(address: address, token: pairingToken)
            try savePairingToken()
            disconnect()
            let current = UUID(); generation = current
            connection = next; connected = true; error = nil
            UserDefaults.standard.set(address, forKey: "liveBridgeAddress")
            pollTask = Task { [weak self] in
                while !Task.isCancelled {
                    guard let self, self.generation == current else { return }
                    await self.refresh(next, generation: current)
                    try? await Task.sleep(for: .milliseconds(300))
                }
            }
        } catch { self.error = error.localizedDescription }
    }
    func disconnect() {
        pollTask?.cancel(); pollTask = nil; generation = UUID(); connection = nil
        connected = false; image = nil; state = nil; evidenceImages = []; evidenceTimes = []
        evidenceID = ""; lastSequence = -1; registrationSent = ""
    }
    private func refresh(_ connection: LiveConnection, generation: UUID) async {
        do {
            let data = try await fetch("api/state", connection: connection)
            let next = try JSONDecoder().decode(LiveState.self, from: data)
            guard self.generation == generation, !Task.isCancelled else { return }
            if state?.monitor.run_id != next.monitor.run_id {
                evidenceID = ""; evidenceImages = []; evidenceTimes = []
            }
            state = next; error = nil
            for alert in next.alerts where !seenAlerts.contains(alert.id) {
                // APNs can alert while the app is closed. If it is unavailable,
                // a freshly observed detection can still alert locally in-app.
                let needsLocal = !next.push_configured || !next.push_registered ||
                    ["failed", "not_configured", "queue_full"].contains(alert.delivery)
                if needsLocal && Date().timeIntervalSince1970 - alert.received_at < 15 {
                    let content = UNMutableNotificationContent()
                    content.title = "Vision · Detection"; content.body = String(alert.summary.prefix(500)); content.sound = .default
                    try? await UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: alert.id, content: content, trigger: nil))
                }
                if needsLocal || alert.delivery == "accepted_by_apns" {
                    seenAlerts.append(alert.id); seenAlerts = Array(seenAlerts.suffix(200))
                    UserDefaults.standard.set(seenAlerts, forKey: "liveSeenAlerts")
                }
            }
            if next.camera.status == "streaming", next.camera.sequence != lastSequence {
                let frame = try await fetch("api/frame", connection: connection)
                guard self.generation == generation, !Task.isCancelled else { return }
                image = UIImage(data: frame); lastSequence = next.camera.sequence
            } else if next.camera.status != "streaming" { image = nil }
            if let evidence = next.evidence.last {
                let key = next.monitor.run_id + ":" + evidence.request_id
                if key != evidenceID {
                    var images: [UIImage] = []
                    for index in evidence.times.indices {
                        let jpeg = try await fetch("api/evidence/\(evidence.request_id)/\(index)", connection: connection)
                        guard self.generation == generation, !Task.isCancelled else { return }
                        if let image = UIImage(data: jpeg) { images.append(image) }
                    }
                    evidenceImages = images; evidenceTimes = evidence.times; evidenceID = key
                }
            }
            let push = PushRegistration.shared
            if !push.token.isEmpty && registrationSent != push.token {
                _ = try await fetch("api/registration", connection: connection, body: [
                    "deviceToken": push.token, "topic": "com.vision.lab", "environment": PushRegistration.environment])
                guard self.generation == generation else { return }
                registrationSent = push.token
            }
        } catch {
            guard self.generation == generation, !Task.isCancelled else { return }
            self.error = "Bridge disconnected or unavailable. Check the Mac and pairing. " + error.localizedDescription
            image = nil; state = nil
        }
    }
    func monitor(start: Bool) async {
        guard let connection else { return }
        busy = true; defer { busy = false }
        do {
            if start {
                _ = try await fetch("api/monitor", connection: connection,
                    body: ["goal": goal, "model": model, "max_calls": maxCalls])
                UserDefaults.standard.set(goal, forKey: "liveGoal")
                UserDefaults.standard.set(model, forKey: "liveModel")
            } else { _ = try await fetch("api/stop", connection: connection, body: [:]) }
            error = nil
        } catch { self.error = error.localizedDescription }
    }
    var active: Bool { ["watching", "analyzing", "stopping", "budget_exhausted"].contains(state?.monitor.phase ?? "") }
}

struct LiveCameraView: View {
    @StateObject private var live = LiveCameraModel()
    @ObservedObject private var push = PushRegistration.shared
    @Environment(\.scenePhase) private var scenePhase
    @State private var showPairing = false
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    camera
                    if let state = live.state { status(state) }
                    if let error = live.error ?? live.state?.error {
                        Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                    }
                    monitoring
                    evidence
                    results
                }.padding()
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("Live camera")
            .toolbar { Button("Pair Mac", systemImage: "link") { showPairing = true } }
            .sheet(isPresented: $showPairing) { pairing }
            .task { if !live.address.isEmpty && !live.pairingToken.isEmpty { live.connect() } }
            .onChange(of: scenePhase) { _, phase in
                if phase == .background { live.disconnect() }
                else if phase == .active && !live.address.isEmpty && !live.pairingToken.isEmpty { live.connect() }
            }
        }
    }
    private var camera: some View {
        ZStack(alignment: .bottomLeading) {
            RoundedRectangle(cornerRadius: 20).fill(.black).aspectRatio(16 / 10, contentMode: .fit)
            if let image = live.image {
                Image(uiImage: image).resizable().scaledToFit().clipShape(RoundedRectangle(cornerRadius: 20))
            } else {
                ContentUnavailableView(live.connected ? "Waiting for a fresh frame" : "Your camera, inside Vision",
                    systemImage: "video", description: Text("Pair with the camera bridge running on your Mac.")).foregroundStyle(.white)
            }
            Text("LIVE · sampled preview").font(.caption.bold()).padding(8).background(.black.opacity(0.7)).foregroundStyle(.white).clipShape(Capsule()).padding(12)
        }.accessibilityIdentifier("liveCameraPreview")
    }
    private func status(_ state: LiveState) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(state.monitor.phase.replacingOccurrences(of: "_", with: " ").capitalized,
                  systemImage: state.monitor.pending.isEmpty ? "eye" : "sparkles")
                .font(.headline).foregroundStyle(state.monitor.pending.isEmpty ? Color.primary : Color.blue)
            Text("Camera: \(state.camera.status) · \(state.monitor.pending.count) analysis requests pending")
            Text("\(state.monitor.calls)/\(state.monitor.max_calls) calls · \(state.monitor.input_tokens) input + \(state.monitor.output_tokens) output tokens")
                .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            if let result = state.results.first, let cutoff = result.observed_until {
                Text("Latest analysis covers through \(cutoff, specifier: "%.1f")s of this session. Replies can lag behind the preview.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }.accessibilityIdentifier("liveAnalysisStatus")
    }
    private var monitoring: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("What should Qwen watch for?").font(.headline)
            TextEditor(text: $live.goal).frame(minHeight: 85).padding(6).background(.background).clipShape(RoundedRectangle(cornerRadius: 12))
                .disabled(live.active).accessibilityIdentifier("liveMonitoringGoal")
            Picker("Model", selection: $live.model) {
                Text("Qwen3-VL Plus").tag("qwen3-vl-plus")
                Text("Qwen3-VL Flash").tag("qwen3-vl-flash")
                Text("Qwen3-VL 8B").tag("qwen3-vl-8b-instruct")
                Text("Qwen3-VL 32B").tag("qwen3-vl-32b-instruct")
            }.disabled(live.active)
            Stepper("Request limit: \(live.maxCalls)", value: $live.maxCalls, in: 1...300).disabled(live.active)
            Button(live.active ? "Stop monitoring" : "Start monitoring", systemImage: live.active ? "stop.fill" : "play.fill") {
                Task { await live.monitor(start: !live.active) }
            }.buttonStyle(.borderedProminent).disabled(!live.connected || live.state == nil || live.busy || live.state?.monitor.phase == "stopping")
                .accessibilityIdentifier("liveMonitorButton")
            Button("Enable detection alerts", systemImage: "bell.badge") { Task { await push.register() } }
            if let state = live.state {
                Text(state.push_configured && state.push_registered ? "Remote alerts are configured. Apple acceptance is shown on each detection." :
                     "Detections appear below. Locked-phone alerts need APNs configured on the Mac and notifications enabled here.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Text("The Mac keeps monitoring when you leave this screen. Stop monitoring before changing the goal. Selected images go to your configured Qwen service.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
    private var evidence: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Sent to Qwen").font(.headline)
            if live.evidenceImages.isEmpty { Text("The exact submitted frames will appear here.").foregroundStyle(.secondary) }
            ScrollView(.horizontal) {
                HStack {
                    ForEach(live.evidenceImages.indices, id: \.self) { index in
                        VStack {
                            Image(uiImage: live.evidenceImages[index]).resizable().scaledToFit().frame(width: 140, height: 95).clipShape(RoundedRectangle(cornerRadius: 8))
                            if index < live.evidenceTimes.count { Text("\(live.evidenceTimes[index], specifier: "%.1f")s").font(.caption.monospacedDigit()) }
                        }
                    }
                }
            }
        }
    }
    private var results: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Detections & analysis").font(.headline)
            ForEach(live.state?.alerts ?? []) { alert in
                VStack(alignment: .leading, spacing: 5) {
                    Label(alert.summary, systemImage: "bell.fill").font(.headline)
                    Text(alert.evidence)
                    Text("Push: " + alert.delivery.replacingOccurrences(of: "_", with: " ")).font(.caption).foregroundStyle(.secondary)
                }.padding().background(Color.orange.opacity(0.12)).clipShape(RoundedRectangle(cornerRadius: 12))
            }
            ForEach(live.state?.results ?? []) { result in
                VStack(alignment: .leading, spacing: 5) {
                    Text(result.action?.capitalized ?? "Analysis failed").font(.headline)
                    Text(result.event_summary ?? result.message ?? "No completed interpretation")
                    if let evidence = result.evidence { Text(evidence).font(.callout).foregroundStyle(.secondary) }
                    Text([result.stage, result.model, result.latency_seconds.map { String(format: "%.1fs response", $0) }, result.provider_code].compactMap { $0 }.joined(separator: " · "))
                        .font(.caption).foregroundStyle(.secondary)
                }.padding().frame(maxWidth: .infinity, alignment: .leading).background(.background).clipShape(RoundedRectangle(cornerRadius: 12))
            }
        }
    }
    private var pairing: some View {
        NavigationStack {
            Form {
                Section("Mac camera bridge") {
                    TextField("http://your-mac.local:8765", text: $live.address).textContentType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                    SecureField("Pairing token", text: $live.pairingToken).textContentType(.oneTimeCode).textInputAutocapitalization(.never).autocorrectionDisabled()
                    Text("Start the bridge on your Mac, then copy local/gateway/pairing-token. Keep both devices on trusted Wi-Fi. Local HTTP is not encrypted.").font(.caption)
                }
                if let error = live.error { Text(error).foregroundStyle(.orange) }
                Button("Connect camera") { live.connect(); if live.connected { showPairing = false } }
                if live.connected { Button("Disconnect preview") { live.disconnect(); showPairing = false } }
            }.navigationTitle("Pair camera bridge").toolbar { Button("Done") { showPairing = false } }
        }
    }
}
