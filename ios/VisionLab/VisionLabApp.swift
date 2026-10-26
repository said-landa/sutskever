import SwiftUI
import UserNotifications
import Security

@main
struct VisionLabApp: App {
    @UIApplicationDelegateAdaptor(PushAppDelegate.self) private var appDelegate
    @StateObject private var settings = ModelSettings()
    @StateObject private var analysis = VideoAnalysis()
    var body: some Scene {
        WindowGroup {
            TabView {
                MonitorView(analysis: analysis, settings: settings)
                    .tabItem { Label("Monitor", systemImage: "eye") }
            }
        }
    }
}

@MainActor
final class PushRegistration: ObservableObject {
    static let shared = PushRegistration()
    @Published var token = ""
    @Published var status = "Register this iPhone for remote notifications."
    func register() async {
        do {
            let allowed = try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])
            guard allowed else { status = "Enable notifications for Vision Lab in iPhone Settings."; return }
            status = "Registering with Apple…"
            UIApplication.shared.registerForRemoteNotifications()
        } catch { status = "Registration failed: " + error.localizedDescription }
    }
    func received(_ data: Data) {
        token = data.map { String(format: "%02x", $0) }.joined()
        do {
            let folder = try FileManager.default.url(for: .applicationSupportDirectory,
                in: .userDomainMask, appropriateFor: nil, create: true)
            let payload: [String: String] = ["deviceToken": token, "topic": "com.vision.lab",
                "environment": "sandbox", "registeredAt": ISO8601DateFormatter().string(from: Date())]
            let json = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
            try json.write(to: folder.appendingPathComponent("apns-registration.json"), options: [.atomic, .completeFileProtection])
            status = "Registered with Apple · Sandbox. Ready for a remote test."
        } catch { status = "Registered, but token export failed: " + error.localizedDescription }
    }
}

@MainActor
final class PushAppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        // Refresh the token on every launch after the user has enabled remote registration.
        if UserDefaults.standard.bool(forKey: "pushRegistrationEnabled") {
            application.registerForRemoteNotifications()
        }
        BackgroundVideoTransfer.shared.reconnect()
        return true
    }
    func application(_ application: UIApplication, handleEventsForBackgroundURLSession identifier: String,
                     completionHandler: @escaping () -> Void) {
        guard identifier == BackgroundVideoTransfer.identifier else { completionHandler(); return }
        BackgroundVideoTransfer.shared.handleBackgroundEvents(completion: completionHandler)
    }
    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        UserDefaults.standard.set(true, forKey: "pushRegistrationEnabled")
        PushRegistration.shared.received(deviceToken)
    }
    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        PushRegistration.shared.status = "Apple registration failed: " + error.localizedDescription
    }
}


enum KeyStore {
    static let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "com.vision.lab", kSecAttrAccount as String: "model-api-key"]
    static func read() -> String {
        var q = query
        q[kSecReturnData as String] = true
        var result: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return "" }
        return String(data: data, encoding: .utf8) ?? ""
    }
    static func save(_ value: String) throws {
        if value.isEmpty {
            let status = SecItemDelete(query as CFDictionary)
            guard status == errSecSuccess || status == errSecItemNotFound else {
                throw NSError(domain: NSOSStatusErrorDomain, code: Int(status))
            }
            return
        }
        let updates: [String: Any] = [kSecValueData as String: Data(value.utf8),
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
        let update = SecItemUpdate(query as CFDictionary, updates as CFDictionary)
        if update == errSecSuccess { return }
        guard update == errSecItemNotFound else {
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(update))
        }
        var q = query
        q[kSecValueData as String] = Data(value.utf8)
        q[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        let status = SecItemAdd(q as CFDictionary, nil)
        guard status == errSecSuccess else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(status)) }
    }
}

@MainActor
final class ModelSettings: ObservableObject {
    @Published var prompt = UserDefaults.standard.string(forKey: "rule") ?? "Describe what happens in this video."
    @Published var model = UserDefaults.standard.string(forKey: "model") ?? VideoModelConnection.alibabaModel
    @Published var apiKey = KeyStore.read()
    private let endpoint = UserDefaults.standard.string(forKey: "endpoint") ?? VideoModelConnection.alibabaEndpoint

    func connection() throws -> VideoModelConnection {
        try VideoModelConnection(endpoint: endpoint.isEmpty ? VideoModelConnection.alibabaEndpoint : endpoint,
                                 model: model, apiKey: apiKey)
    }
    var maximumVideoBytes: Int {
        VideoModelConnection.maximumVideoBytes(for: endpoint.isEmpty ? VideoModelConnection.alibabaEndpoint : endpoint)
    }
    func save() throws {
        try KeyStore.save(apiKey)
        UserDefaults.standard.set(prompt, forKey: "rule")
        UserDefaults.standard.set(model, forKey: "model")
    }
    func savePrompt() { UserDefaults.standard.set(prompt, forKey: "rule") }
    func refreshKey() { apiKey = KeyStore.read() }
}

struct ConnectionView: View {
    @ObservedObject var settings: ModelSettings
    @Environment(\.dismiss) private var dismiss
    @State private var error: String?
    var body: some View {
        NavigationStack {
            Form {
                Picker("Model", selection: $settings.model) {
                    ForEach(VideoModelConnection.modelOptions, id: \.id) { option in
                        Text(option.name).tag(option.id)
                    }
                    if !VideoModelConnection.modelOptions.contains(where: { $0.id == settings.model }) {
                        Text(settings.model).tag(settings.model)
                    }
                }.accessibilityIdentifier("modelPicker")
                SecureField("API key", text: $settings.apiKey)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                if let error { Text(error).foregroundStyle(.red) }
            }.navigationTitle("Connection").toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        do { try settings.save(); dismiss() }
                        catch { self.error = error.localizedDescription }
                    }
                }
            }
        }
    }
}
