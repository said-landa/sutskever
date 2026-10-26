import Foundation

enum VideoTransferPhase: String, Codable, Sendable {
    case uploading, processing, completed, failed, cancelled
    var pending: Bool { self == .uploading || self == .processing }
}

struct VideoTransferRecord: Codable, Sendable {
    let id: UUID
    let name: String
    let prompt: String
    let model: String
    let created: Date
    var phase: VideoTransferPhase
    var progress: Double
    var response: VideoModelResponse?
    var error: String?

    init(id: UUID = UUID(), name: String, prompt: String, model: String) {
        self.id = id
        self.name = name
        self.prompt = prompt
        self.model = model
        self.created = Date()
        self.phase = .uploading
        self.progress = 0
    }
}

/// Only analysis metadata and results are serialized here; authentication stays in Keychain/URLSession.
struct VideoTransferStore: Sendable {
    let directory: URL
    static func applicationStore() throws -> Self {
        let root = try FileManager.default.url(for: .applicationSupportDirectory,
            in: .userDomainMask, appropriateFor: nil, create: true)
        return Self(directory: root.appendingPathComponent("VideoAnalysis", isDirectory: true))
    }
    func save(_ record: VideoTransferRecord) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        var options: Data.WritingOptions = [.atomic]
        #if os(iOS)
        options.insert(.completeFileProtectionUntilFirstUserAuthentication)
        #endif
        try JSONEncoder().encode(record).write(to: directory.appendingPathComponent("latest.json"), options: options)
    }
    func load() throws -> VideoTransferRecord? {
        let path = directory.appendingPathComponent("latest.json")
        guard FileManager.default.fileExists(atPath: path.path) else { return nil }
        return try JSONDecoder().decode(VideoTransferRecord.self, from: Data(contentsOf: path))
    }
    func bodyURL(for id: UUID) -> URL { directory.appendingPathComponent("request-\(id).json") }
    func responseURL(for id: UUID) -> URL { directory.appendingPathComponent("response-\(id).json") }
    func removePayloads(for id: UUID) {
        try? FileManager.default.removeItem(at: bodyURL(for: id))
        try? FileManager.default.removeItem(at: responseURL(for: id))
    }
}
