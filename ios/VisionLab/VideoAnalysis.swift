import SwiftUI
import AVKit
import PhotosUI
import CoreTransferable
import UniformTypeIdentifiers

struct ImportedMovie: Transferable {
    let url: URL
    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(contentType: .movie) { movie in
            SentTransferredFile(movie.url)
        } importing: { received in
            let copy = VideoFiles.temporaryURL(extension: received.file.pathExtension)
            try FileManager.default.copyItem(at: received.file, to: copy)
            return ImportedMovie(url: copy)
        }
    }
}

enum VideoFiles {
    static func temporaryURL(extension suffix: String) -> URL {
        FileManager.default.temporaryDirectory.appendingPathComponent("vision-video-\(UUID())")
            .appendingPathExtension(suffix.isEmpty ? "mov" : suffix)
    }
    static func copyImportedFile(_ source: URL) throws -> URL {
        let access = source.startAccessingSecurityScopedResource()
        defer { if access { source.stopAccessingSecurityScopedResource() } }
        try Task.checkCancellation()
        let copy = temporaryURL(extension: source.pathExtension)
        do {
            try FileManager.default.copyItem(at: source, to: copy)
            try Task.checkCancellation()
            return copy
        } catch {
            try? FileManager.default.removeItem(at: copy)
            throw error
        }
    }
}

struct PreparedVideo: Sendable {
    let url: URL
    let duration: Double
    let bytes: Int
}

actor VideoPreparer {
    func prepare(_ source: URL, maximumBytes: Int) async throws -> PreparedVideo {
        try Task.checkCancellation()
        let asset = AVURLAsset(url: source)
        let duration = try await asset.load(.duration).seconds
        guard duration.isFinite, duration > 0,
              !(try await asset.loadTracks(withMediaType: .video)).isEmpty else {
            throw VideoAnalysisError.message("This file has no readable video. Choose another video.")
        }
        guard let exporter = AVAssetExportSession(asset: asset, presetName: AVAssetExportPreset1280x720),
              exporter.supportedFileTypes.contains(.mp4) else {
            throw VideoAnalysisError.message("This video format cannot be prepared on this device. Try an MP4 or MOV video.")
        }
        let output = VideoFiles.temporaryURL(extension: "mp4")
        exporter.shouldOptimizeForNetworkUse = true
        exporter.fileLengthLimit = Int64(maximumBytes)
        let cancellation = ExportCancellation(exporter)
        do {
            try await withTaskCancellationHandler {
                if #available(iOS 18.0, *) {
                    try await exporter.export(to: output, as: .mp4)
                } else {
                    exporter.outputURL = output
                    exporter.outputFileType = .mp4
                    try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                        exporter.exportAsynchronously {
                            switch cancellation.exporter.status {
                            case .completed: continuation.resume()
                            case .cancelled: continuation.resume(throwing: CancellationError())
                            default: continuation.resume(throwing: cancellation.exporter.error ?? VideoAnalysisError.message("Video preparation failed."))
                            }
                        }
                    }
                }
            } onCancel: { cancellation.cancel() }
            try Task.checkCancellation()
            let size = try FileManager.default.attributesOfItem(atPath: output.path)[.size] as? NSNumber
            guard let size, size.intValue > 0 else {
                throw VideoAnalysisError.message("Video preparation produced an empty file. Choose another video.")
            }
            guard size.intValue <= maximumBytes else {
                throw VideoAnalysisError.message("This video could not fit your connection's upload limit. Choose a shorter video for this experiment.")
            }
            return PreparedVideo(url: output, duration: duration, bytes: size.intValue)
        } catch {
            try? FileManager.default.removeItem(at: output)
            throw error
        }
    }
}

/// cancelExport is safe to call from the cancellation handler while export is in progress.
private final class ExportCancellation: @unchecked Sendable {
    let exporter: AVAssetExportSession
    init(_ exporter: AVAssetExportSession) { self.exporter = exporter }
    func cancel() { exporter.cancelExport() }
}

@MainActor
final class VideoAnalysis: ObservableObject {
    @Published private(set) var busy = false
    @Published private(set) var preparing = false
    @Published private(set) var status = ""
    @Published private(set) var error: String?
    @Published private(set) var response: VideoModelResponse?
    @Published private(set) var uploadProgress: Double?
    private let preparer = VideoPreparer()
    private let transfer = BackgroundVideoTransfer.shared
    private var task: Task<Void, Never>?
    private var activeID: UUID?
    private var preparationTime: UIBackgroundTaskIdentifier = .invalid

    init() {
        do {
            try transfer.observe { [weak self] record in
                Task { @MainActor in
                    guard let self else { return }
                    // Read the latest durable state rather than replaying an older progress callback.
                    self.apply((try? self.transfer.store.load()) ?? record)
                }
            }
        } catch { self.error = "Could not restore the last analysis. Check available storage." }
    }

    func uploadFile(_ source: URL, connection: VideoModelConnection, prompt: String) {
        guard !busy else { return }
        beginPreparation()
        let id = activeID!
        task = Task {
            do {
                let copier = Task.detached(priority: .userInitiated) { try VideoFiles.copyImportedFile(source) }
                let copy = try await withTaskCancellationHandler { try await copier.value } onCancel: { copier.cancel() }
                defer { try? FileManager.default.removeItem(at: copy) }
                try Task.checkCancellation()
                try await submit(copy, name: source.lastPathComponent, id: id, connection: connection, prompt: prompt)
            } catch { finishFailure(error, id: id) }
        }
    }

    func uploadPhoto(_ item: PhotosPickerItem, connection: VideoModelConnection, prompt: String) {
        guard !busy else { return }
        beginPreparation()
        let id = activeID!
        status = "Loading video…"
        task = Task {
            do {
                guard let movie = try await item.loadTransferable(type: ImportedMovie.self) else {
                    throw VideoAnalysisError.message("Photos could not load this video. Check your connection and try again.")
                }
                defer { try? FileManager.default.removeItem(at: movie.url) }
                try Task.checkCancellation()
                try await submit(movie.url, name: "Selected video", id: id, connection: connection, prompt: prompt)
            } catch { finishFailure(error, id: id) }
        }
    }

    private func beginPreparation() {
        activeID = UUID()
        response = nil
        error = nil
        busy = true
        preparing = true
        uploadProgress = nil
        status = "Preparing video…"
        preparationTime = UIApplication.shared.beginBackgroundTask(withName: "Prepare video upload") { [weak self] in
            Task { @MainActor in
                guard let self, self.preparing else { return }
                self.cancel()
                self.error = "Video preparation was interrupted. Open the app and upload it again."
            }
        }
    }

    private func submit(_ source: URL, name: String, id: UUID, connection: VideoModelConnection, prompt: String) async throws {
        guard id == activeID else { throw CancellationError() }
        let video = try await preparer.prepare(source, maximumBytes: connection.maximumVideoBytes)
        defer { try? FileManager.default.removeItem(at: video.url) }
        try VideoModelClient.validateDuration(video.duration, connection: connection)
        try Task.checkCancellation()
        status = "Preparing upload…"
        let builder = Task.detached(priority: .userInitiated) {
            try VideoModelClient.makeUploadFile(videoURL: video.url, duration: video.duration, model: connection.model,
                maximumBytes: connection.maximumVideoBytes, disableThinking: connection.disableThinking, analysisPrompt: prompt)
        }
        let body = try await withTaskCancellationHandler { try await builder.value } onCancel: { builder.cancel() }
        defer { try? FileManager.default.removeItem(at: body) }
        try Task.checkCancellation()
        guard id == activeID else { throw CancellationError() }
        let record = VideoTransferRecord(id: id, name: name, prompt: prompt, model: connection.model)
        try transfer.enqueue(body: body, request: VideoModelClient.makeRequest(connection: connection), record: record)
        preparing = false
        endPreparationTime()
        apply(record)
    }

    private func apply(_ record: VideoTransferRecord) {
        if let activeID, record.id != activeID { return }
        activeID = record.id
        busy = record.phase.pending
        uploadProgress = record.progress
        response = record.response
        error = record.error
        switch record.phase {
        case .uploading: status = "Uploading video · \(Int(record.progress * 100))%"
        case .processing: status = "Analyzing…"
        case .completed: status = ""
        case .failed: status = ""
        case .cancelled: status = "Cancelled."
        }
    }

    func reportError(_ failure: Error) { error = failure.localizedDescription }

    func cancel() {
        if preparing {
            activeID = nil
            task?.cancel()
            task = nil
            preparing = false
            busy = false
            status = "Cancelled."
            uploadProgress = nil
            endPreparationTime()
        } else { transfer.cancel() }
    }

    func reconnect() {
        guard !preparing else { return }
        if let record = try? transfer.store.load() { apply(record) }
        transfer.reconnect()
    }

    private func endPreparationTime() {
        guard preparationTime != .invalid else { return }
        UIApplication.shared.endBackgroundTask(preparationTime)
        preparationTime = .invalid
    }

    private func finishFailure(_ failure: Error, id: UUID) {
        guard id == activeID else { return }
        preparing = false
        busy = false
        uploadProgress = nil
        endPreparationTime()
        status = ""
        if failure is CancellationError || (failure as? URLError)?.code == .cancelled { status = "Cancelled." }
        else { error = failure.localizedDescription }
    }
}

struct MonitorView: View {
    @ObservedObject var analysis: VideoAnalysis
    @ObservedObject var settings: ModelSettings
    @State private var photo: PhotosPickerItem?
    @State private var importing = false
    @State private var connectionSheet = false
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    TextEditor(text: $settings.prompt)
                        .frame(minHeight: 130).padding(8)
                        .background(.thinMaterial).clipShape(RoundedRectangle(cornerRadius: 12))
                        .accessibilityLabel("What to watch for or analyze")
                        .accessibilityIdentifier("analysisPrompt")
                        .disabled(analysis.busy)
                    Menu {
                        PhotosPicker(selection: $photo, matching: .videos, preferredItemEncoding: .current) {
                            Label("Photos", systemImage: "photo.on.rectangle")
                        }
                        Button("Files", systemImage: "folder") { importing = true }
                    } label: {
                        Label("Upload video", systemImage: "square.and.arrow.up")
                    }.buttonStyle(.borderedProminent).disabled(analysis.busy)
                        .accessibilityIdentifier("uploadVideo")
                    if analysis.busy {
                        HStack {
                            ProgressView()
                            Text(analysis.status).font(.callout)
                        }
                        if let progress = analysis.uploadProgress, progress < 1 { ProgressView(value: progress) }
                        Button("Cancel", role: .cancel) { analysis.cancel() }.buttonStyle(.bordered)
                    }
                    if let error = analysis.error {
                        Text(error).foregroundStyle(.red).textSelection(.enabled)
                            .accessibilityIdentifier("videoAnalysisError")
                    }
                    if let response = analysis.response {
                        Text(response.text).textSelection(.enabled)
                            .padding().frame(maxWidth: .infinity, alignment: .leading)
                            .background(.thinMaterial).clipShape(RoundedRectangle(cornerRadius: 12))
                            .accessibilityIdentifier("videoDescription")
                        if response.truncated {
                            Text("The model reached its response limit; the result may be incomplete.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }.padding()
            }.navigationTitle("Monitor")
                .toolbar { Button("Connection", systemImage: "gear") { connectionSheet = true } }
                .sheet(isPresented: $connectionSheet) { ConnectionView(settings: settings) }
                .onChange(of: settings.prompt) { _, _ in settings.savePrompt() }
                .onChange(of: photo) { _, item in
                    guard let item else { return }
                    do {
                        let connection = try settings.connection()
                        try settings.save()
                        analysis.uploadPhoto(item, connection: connection, prompt: settings.prompt)
                    } catch { analysis.reportError(error) }
                    photo = nil
                }
                .fileImporter(isPresented: $importing, allowedContentTypes: [.movie]) { result in
                    do {
                        let source = try result.get()
                        let connection = try settings.connection()
                        try settings.save()
                        analysis.uploadFile(source, connection: connection, prompt: settings.prompt)
                    } catch { analysis.reportError(error) }
                }
                .onChange(of: scenePhase) { _, phase in
                    if phase == .active {
                        analysis.reconnect()
                        if !connectionSheet { settings.refreshKey() }
                    }
                }
        }
    }
}
