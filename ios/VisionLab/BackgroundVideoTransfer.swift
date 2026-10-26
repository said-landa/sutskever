import Foundation

/// File-backed URLSession uploads continue outside the foreground and reconnect after system relaunch.
final class BackgroundVideoTransfer: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    static let identifier = "com.vision.lab.video-analysis.v1"
    private static let interruptedMessage = "The transfer was interrupted before its response was saved. Upload the video again."
    static let shared: BackgroundVideoTransfer = {
        do { return try BackgroundVideoTransfer(store: .applicationStore()) }
        catch { fatalError("Could not open local analysis storage.") }
    }()
    let store: VideoTransferStore
    private let lock = NSLock()
    private var session: URLSession!
    private var handler: (@Sendable (VideoTransferRecord) -> Void)?
    private var backgroundCompletion: (() -> Void)?
    private var storageFailures: [UUID: String] = [:]

    init(store: VideoTransferStore, configuration: URLSessionConfiguration? = nil) throws {
        self.store = store
        super.init()
        try FileManager.default.createDirectory(at: store.directory, withIntermediateDirectories: true)
        let configuration = configuration ?? .background(withIdentifier: Self.identifier)
        configuration.isDiscretionary = false
        configuration.timeoutIntervalForRequest = 600
        configuration.timeoutIntervalForResource = 900
        #if os(iOS)
        configuration.sessionSendsLaunchEvents = true
        #endif
        let queue = OperationQueue()
        queue.maxConcurrentOperationCount = 1
        queue.name = "vision.video-transfer"
        session = URLSession(configuration: configuration, delegate: self, delegateQueue: queue)
    }

    func observe(_ handler: @escaping @Sendable (VideoTransferRecord) -> Void) throws {
        lock.lock()
        self.handler = handler
        let record: VideoTransferRecord?
        do { record = try store.load() }
        catch { lock.unlock(); throw error }
        lock.unlock()
        if let record { handler(record) }
        reconnect()
    }

    func enqueue(body: URL, request: URLRequest, record: VideoTransferRecord) throws {
        lock.lock()
        defer { lock.unlock() }
        if let current = try store.load(), current.phase.pending {
            throw VideoAnalysisError.message("An analysis is already running. Wait for its response or cancel it.")
        }
        if let current = try store.load() { store.removePayloads(for: current.id) }
        let upload = store.bodyURL(for: record.id)
        do {
            try FileManager.default.moveItem(at: body, to: upload)
            #if os(iOS)
            try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
                                                  ofItemAtPath: upload.path)
            #endif
            try store.save(record)
        } catch {
            try? FileManager.default.removeItem(at: upload)
            throw error
        }
        let task = session.uploadTask(with: request, fromFile: upload)
        task.taskDescription = record.id.uuidString
        task.resume()
        handler?(record)
    }

    func cancel() {
        lock.lock()
        var id: UUID?
        if var record = try? store.load(), record.phase.pending {
            id = record.id
            record.phase = .cancelled
            record.error = nil
            try? store.save(record)
            handler?(record)
        }
        lock.unlock()
        guard let id else { return }
        session.getAllTasks { tasks in
            tasks.filter { $0.taskDescription == id.uuidString }.forEach { $0.cancel() }
        }
    }

    func reconnect() {
        session.getAllTasks { [weak self] tasks in
            guard let self else { return }
            // Let any completion events already queued by iOS save their results first.
            self.session.delegateQueue.addOperation {
                self.lock.lock()
                defer { self.lock.unlock() }
                guard var record = try? self.store.load(), record.phase.pending,
                      !tasks.contains(where: { $0.taskDescription == record.id.uuidString }),
                      Date().timeIntervalSince(record.created) > 3 else { return }
                record.phase = .failed
                record.error = Self.interruptedMessage
                try? self.store.save(record)
                self.store.removePayloads(for: record.id)
                self.handler?(record)
            }
        }
    }

    func handleBackgroundEvents(completion: @escaping () -> Void) {
        lock.lock(); backgroundCompletion = completion; lock.unlock()
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64,
                    totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        guard totalBytesExpectedToSend > 0 else { return }
        lock.lock(); defer { lock.unlock() }
        guard var record = try? store.load(), record.phase.pending,
              task.taskDescription == record.id.uuidString else { return }
        let progress = min(1, Double(totalBytesSent) / Double(totalBytesExpectedToSend))
        guard progress >= 1 || progress - record.progress >= 0.01 else { return }
        record.progress = progress
        if progress >= 1 { record.phase = .processing }
        do { try store.save(record); handler?(record) }
        catch { storageFailures[record.id] = "Could not save transfer progress. Check available storage." }
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        lock.lock(); defer { lock.unlock() }
        guard let record = try? store.load(), record.phase.pending,
              dataTask.taskDescription == record.id.uuidString else { return }
        do {
            let url = store.responseURL(for: record.id)
            if !FileManager.default.fileExists(atPath: url.path) {
                var options: Data.WritingOptions = []
                #if os(iOS)
                options.insert(.completeFileProtectionUntilFirstUserAuthentication)
                #endif
                try Data().write(to: url, options: options)
            }
            let file = try FileHandle(forWritingTo: url)
            defer { try? file.close() }
            let size = try file.seekToEnd()
            guard size + UInt64(data.count) <= 2 * 1024 * 1024 else {
                throw VideoAnalysisError.message("The model response was too large to save.")
            }
            try file.write(contentsOf: data)
        } catch {
            storageFailures[record.id] = "Could not save the model response. Check available storage and try again."
            dataTask.cancel()
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        lock.lock(); defer { lock.unlock() }
        guard let description = task.taskDescription, let id = UUID(uuidString: description) else { return }
        defer { store.removePayloads(for: id); storageFailures[id] = nil }
        guard var record = try? store.load(), id == record.id else { return }
        guard record.phase.pending || (record.phase == .failed && record.error == Self.interruptedMessage) else { return }
        do {
            if let failure = storageFailures[record.id] { throw VideoAnalysisError.message(failure) }
            if let error { throw error }
            let data = try Data(contentsOf: store.responseURL(for: record.id))
            record.response = try VideoModelClient.decodeCompletion(data, response: task.response)
            record.phase = .completed
            record.progress = 1
            record.error = nil
        } catch {
            record.phase = .failed
            if let failure = error as? VideoAnalysisError { record.error = failure.localizedDescription }
            else if (error as? URLError)?.code == .timedOut {
                record.error = "The model took too long to respond. Try a shorter video."
            } else if (error as? URLError)?.code == .cancelled {
                record.error = "The transfer was interrupted. Upload the video again."
            } else { record.error = "The response could not be received. Check your connection and try again." }
        }
        do { try store.save(record); handler?(record) }
        catch {
            record.phase = .failed
            record.error = "Could not save the analysis result. Check available storage."
            handler?(record)
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }

    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        lock.lock()
        let completion = backgroundCompletion
        backgroundCompletion = nil
        lock.unlock()
        DispatchQueue.main.async { completion?() }
    }

    func invalidateForTesting() { session.invalidateAndCancel() }
}
