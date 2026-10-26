import Foundation

private struct CheckFailure: Error, CustomStringConvertible {
    let description: String
}

private func check(_ condition: @autoclosure () throws -> Bool, _ message: String) throws {
    if try condition() == false { throw CheckFailure(description: message) }
}

private func expectFailure(_ message: String, _ work: () throws -> Void) throws {
    do { try work() } catch { return }
    throw CheckFailure(description: message)
}

/// Transport fixtures only; these responses do not perform or claim model inference.
private final class CompletionFixture: URLProtocol, @unchecked Sendable {
    static let lock = NSLock()
    static var status = 200
    static var response = Data()
    static var delay: Double = 0
    static var captured: URLRequest?
    private var delivery: DispatchWorkItem?

    static func configure(status: Int = 200, body: String, delay: Double = 0) {
        lock.lock(); defer { lock.unlock() }
        self.status = status; self.response = Data(body.utf8); self.delay = delay; self.captured = nil
    }
    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "vision.example" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.lock.lock()
        Self.captured = request
        let status = Self.status, data = Self.response, delay = Self.delay
        Self.lock.unlock()
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            let response = HTTPURLResponse(url: self.request.url!, statusCode: status,
                                           httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!
            self.client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            self.client?.urlProtocol(self, didLoad: data)
            self.client?.urlProtocolDidFinishLoading(self)
        }
        delivery = work
        DispatchQueue.global().asyncAfter(deadline: .now() + delay, execute: work)
    }
    override func stopLoading() { delivery?.cancel() }
    static func lastRequest() -> URLRequest? {
        lock.lock(); defer { lock.unlock() }; return captured
    }
}

@main
struct VideoWiringChecks {
    static func main() async throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("vision-wiring-check-\(UUID())")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let video = folder.appendingPathComponent("fixture.mp4")
        let original = Data((0..<(48 * 1024 * 2 + 7)).map { UInt8($0 % 251) })
        try original.write(to: video)
        let body = try VideoModelClient.makeUploadFile(videoURL: video, duration: 12.5,
                                                       model: "model-with-\"quotes", disableThinking: true)
        defer { try? FileManager.default.removeItem(at: body) }
        let requestJSON = try JSONSerialization.jsonObject(with: Data(contentsOf: body)) as! [String: Any]
        try check(requestJSON["model"] as? String == "model-with-\"quotes", "Model name must survive JSON escaping")
        try check(requestJSON["enable_thinking"] as? Bool == false, "Hosted description should disable thinking")
        let messages = requestJSON["messages"] as! [[String: Any]]
        try check(messages[0]["content"] as? String == VideoModelClient.prompt, "Generic description prompt is missing")
        let content = messages[1]["content"] as! [[String: Any]]
        let videoPart = content.first { $0["type"] as? String == "video_url" }!
        let videoURL = (videoPart["video_url"] as! [String: String])["url"]!
        let prefix = "data:video/mp4;base64,"
        try check(videoURL.hasPrefix(prefix), "Request must contain native MP4 input")
        try check(Data(base64Encoded: String(videoURL.dropFirst(prefix.count))) == original,
                  "Full video bytes must survive multiple base64 chunks, including the final partial chunk")
        try check(!content.contains { $0["type"] as? String == "image_url" }, "Video analysis must not silently switch to sampled images")
        let focused = try VideoModelClient.makeUploadFile(videoURL: video, duration: 12.5, model: "fixture",
            analysisPrompt: "Watch for someone placing an object inside a bag.")
        defer { try? FileManager.default.removeItem(at: focused) }
        let focusedPayload = try JSONSerialization.jsonObject(with: Data(contentsOf: focused)) as! [String: Any]
        let focusedMessages = focusedPayload["messages"] as! [[String: Any]]
        let focusedContent = focusedMessages[1]["content"] as! [[String: Any]]
        try check((focusedContent.first?["text"] as? String)?.contains("placing an object inside a bag") == true,
                  "The editable Monitor prompt must reach the model request")
        try expectFailure("Connection upload limit must be enforced") {
            _ = try VideoModelClient.makeUploadFile(videoURL: video, duration: 12.5, model: "fixture", maximumBytes: 100)
        }
        let empty = folder.appendingPathComponent("empty.mp4")
        try Data().write(to: empty)
        try expectFailure("Empty video must be rejected") {
            _ = try VideoModelClient.makeUploadFile(videoURL: empty, duration: 1, model: "fixture")
        }
        print("PASS: complete native-video payload, JSON escaping, base64 boundaries and size limits")

        let connection = try VideoModelConnection(endpoint: "https://vision.example/v1/chat/completions", model: "fixture", apiKey: "fixture-key")
        for host in ["192.168.1.4", "172.16.0.2", "10.0.0.2", "localhost", "server.local"] {
            _ = try VideoModelConnection(endpoint: "http://\(host):8000/v1/chat/completions", model: "fixture", apiKey: "")
        }
        for endpoint in ["http://public.example/v1/chat/completions", "https://vision.example/v1",
                         "https://user:password@vision.example/v1/chat/completions"] {
            try expectFailure("Invalid endpoint accepted: \(endpoint)") {
                _ = try VideoModelConnection(endpoint: endpoint, model: "fixture", apiKey: "")
            }
        }
        try expectFailure("Hosted connection must require a user API key") {
            _ = try VideoModelConnection(endpoint: VideoModelConnection.alibabaEndpoint, model: VideoModelConnection.alibabaModel, apiKey: "")
        }
        try check(VideoModelConnection.maximumVideoBytes(for: VideoModelConnection.alibabaEndpoint) == 7_000_000,
                  "Hosted payload must fit the 10 MB base64 string limit")
        let hosted = try VideoModelConnection(endpoint: VideoModelConnection.alibabaEndpoint,
            model: VideoModelConnection.alibabaModel, apiKey: "fixture-key")
        let instruct = try VideoModelConnection(endpoint: VideoModelConnection.alibabaEndpoint,
            model: "qwen3-vl-32b-instruct", apiKey: "fixture-key")
        try check(hosted.disableThinking && !instruct.disableThinking, "Only hybrid models should receive the thinking-mode switch")
        try VideoModelClient.validateDuration(60, connection: hosted)
        for duration in [Double.nan, 0, 1, 3601] {
            try expectFailure("Hosted video duration limits must be enforced") {
                try VideoModelClient.validateDuration(duration, connection: hosted)
            }
        }
        try expectFailure("Instruct models must use their 20-minute video limit") {
            try VideoModelClient.validateDuration(1201, connection: instruct)
        }
        print("PASS: endpoint validation, local-network support and hosted key/size requirements")

        let success = """
        {"choices":[{"message":{"content":"Transport fixture response; no model inference."},"finish_reason":"stop"}]}
        """
        let answer = try VideoModelClient.decodeResponse(Data(success.utf8))
        try check(answer.text == "Transport fixture response; no model inference." && !answer.truncated, "Plain description decode failed")
        let structured = """
        {"choices":[{"message":{"content":[{"type":"text","text":"First"},{"type":"text","text":"Second"}]},"finish_reason":"length"}]}
        """
        let parts = try VideoModelClient.decodeResponse(Data(structured.utf8))
        try check(parts.text == "First\nSecond" && parts.truncated, "Structured content and truncation must be preserved")
        let thinking = """
        {"choices":[{"message":{"content":"<think>Internal reasoning</think>Final description"}}]}
        """
        let completed = try VideoModelClient.decodeResponse(Data(thinking.utf8))
        try check(completed.text == "Final description", "Reasoning must not replace the final description")
        for invalid in ["{}", "not json", "{\"choices\":[{\"message\":{\"content\":\"   \"}}]}",
                        "{\"choices\":[{\"message\":{\"content\":\"<think>Unfinished\"}}]}"] {
            try expectFailure("Invalid or empty model response was accepted") { _ = try VideoModelClient.decodeResponse(Data(invalid.utf8)) }
        }
        print("PASS: response text, structured text, truncation, reasoning-only and malformed completions")

        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [CompletionFixture.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let client = VideoModelClient(session: session)
        CompletionFixture.configure(body: success)
        let received = try await client.describe(videoURL: video, duration: 12.5, connection: connection) { _ in }
        try check(received.text == answer.text, "Network response did not reach the caller")
        let sent = CompletionFixture.lastRequest()
        try check(sent?.httpMethod == "POST", "Upload must use POST")
        try check(sent?.value(forHTTPHeaderField: "Authorization") == "Bearer fixture-key", "API key was not applied")
        try check(sent?.value(forHTTPHeaderField: "Content-Type") == "application/json", "Upload content type is wrong")
        for status in [400, 401, 404, 413, 429, 500] {
            CompletionFixture.configure(status: status, body: "{\"error\":\"fixture\"}")
            do {
                _ = try await client.describe(videoURL: video, duration: 12.5, connection: connection) { _ in }
                throw CheckFailure(description: "HTTP \(status) must fail")
            } catch let failure as VideoAnalysisError {
                try check(failure.localizedDescription.contains("HTTP \(status)"), "HTTP status must be visible in the app error")
            }
        }
        CompletionFixture.configure(body: success, delay: 2)
        let pending = Task { try await client.describe(videoURL: video, duration: 12.5, connection: connection) { _ in } }
        try await Task.sleep(for: .milliseconds(100))
        pending.cancel()
        do {
            _ = try await pending.value
            throw CheckFailure(description: "Cancelled upload must not return a result")
        } catch is CancellationError {
        } catch let error as URLError where error.code == .cancelled {
        }
        print("PASS: upload transport, authorization, server failures and cancellation")

        let store = VideoTransferStore(directory: folder.appendingPathComponent("persistent-analysis"))
        let delegateClient = try BackgroundVideoTransfer(store: store, configuration: configuration)
        defer { delegateClient.invalidateForTesting() }
        let record = VideoTransferRecord(name: "fixture.mp4", prompt: "Watch an object", model: "fixture")
        CompletionFixture.configure(body: success, delay: 0.2)
        let diskBody = try VideoModelClient.makeUploadFile(videoURL: video, duration: 12.5, model: "fixture")
        try delegateClient.enqueue(body: diskBody, request: VideoModelClient.makeRequest(connection: connection), record: record)
        try check(try store.load()?.id == record.id, "The request must be recorded before the app can suspend")
        try expectFailure("Simultaneous analyses must not overwrite a pending result") {
            let other = try VideoModelClient.makeUploadFile(videoURL: video, duration: 12.5, model: "fixture")
            defer { try? FileManager.default.removeItem(at: other) }
            try delegateClient.enqueue(body: other, request: VideoModelClient.makeRequest(connection: connection), record: record)
        }
        try await waitForTerminal(store)
        let restored = try VideoTransferStore(directory: store.directory).load()!
        try check(restored.phase == .completed && restored.response?.text == answer.text,
                  "A delegate completion must persist a response that a fresh app instance can read")
        let metadata = String(decoding: try Data(contentsOf: store.directory.appendingPathComponent("latest.json")), as: UTF8.self)
        try check(!metadata.contains("fixture-key") && !metadata.contains("Authorization"), "Persisted analysis metadata must not contain credentials")
        try check(!FileManager.default.fileExists(atPath: store.bodyURL(for: record.id).path), "Completed upload payloads must be removed")

        CompletionFixture.configure(status: 403, body: "{\"error\":{\"code\":\"AllocationQuota.FreeTierOnly\"}}")
        let denied = VideoTransferRecord(name: "fixture.mp4", prompt: "Observe", model: "fixture")
        let deniedBody = try VideoModelClient.makeUploadFile(videoURL: video, duration: 12.5, model: "fixture")
        try delegateClient.enqueue(body: deniedBody, request: VideoModelClient.makeRequest(connection: connection), record: denied)
        try await waitForTerminal(store)
        let failure = try store.load()!
        try check(failure.phase == .failed && failure.error?.contains("free quota") == true,
                  "Quota errors must survive relaunch and distinguish quota from invalid credentials")

        CompletionFixture.configure(body: success, delay: 2)
        let cancelled = VideoTransferRecord(name: "fixture.mp4", prompt: "Observe", model: "fixture")
        let cancelledBody = try VideoModelClient.makeUploadFile(videoURL: video, duration: 12.5, model: "fixture")
        try delegateClient.enqueue(body: cancelledBody, request: VideoModelClient.makeRequest(connection: connection), record: cancelled)
        delegateClient.cancel()
        try await Task.sleep(for: .milliseconds(200))
        try check(try store.load()?.phase == .cancelled, "Late callbacks must not replace a cancelled analysis")
        print("PASS: editable prompt, durable request/result, credential isolation, quota errors and persistent cancellation")
        print("All video wiring checks passed. Transport fixtures do not validate model understanding.")
    }

    private static func waitForTerminal(_ store: VideoTransferStore) async throws {
        let deadline = Date().addingTimeInterval(5)
        while Date() < deadline {
            if let record = try store.load(), !record.phase.pending { return }
            try await Task.sleep(for: .milliseconds(20))
        }
        throw CheckFailure(description: "The delegate did not persist its completion")
    }
}
