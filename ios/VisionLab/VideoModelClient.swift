import Foundation

struct VideoModelConnection: Sendable {
    static let alibabaEndpoint = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
    static let alibabaModel = "qwen3-vl-plus"
    let url: URL
    let model: String
    let apiKey: String
    var isAlibaba: Bool { Self.isAlibabaHost(url.host ?? "") }
    var maximumVideoBytes: Int { Self.maximumVideoBytes(for: url.absoluteString) }
    var maximumDuration: Double {
        model == "qwen3-vl-32b-instruct" || model == "qwen3-vl-8b-instruct" ? 1200 : 3600
    }
    var disableThinking: Bool {
        isAlibaba && (model.hasPrefix("qwen3-vl-plus") || model.hasPrefix("qwen3-vl-flash"))
    }

    static let modelOptions: [(id: String, name: String)] = [
        ("qwen3-vl-plus", "Qwen3-VL-Plus"),
        ("qwen3-vl-32b-instruct", "Qwen3-VL-32B Instruct"),
        ("qwen3-vl-8b-instruct", "Qwen3-VL-8B Instruct")
    ]

    static func maximumVideoBytes(for endpoint: String) -> Int {
        // Alibaba's base64 video string limit is 10 MB; 7 MB stays below it after encoding.
        isAlibabaHost(URL(string: endpoint)?.host ?? "") ? 7_000_000 : VideoModelClient.maximumVideoBytes
    }

    private static func isAlibabaHost(_ host: String) -> Bool {
        host == "dashscope-intl.aliyuncs.com" || host == "dashscope.aliyuncs.com" ||
            host == "dashscope-us.aliyuncs.com" || host == "cn-hongkong.dashscope.aliyuncs.com" ||
            host.hasSuffix(".maas.aliyuncs.com")
    }

    init(endpoint: String, model: String, apiKey: String) throws {
        let endpoint = endpoint.trimmingCharacters(in: .whitespacesAndNewlines)
        let model = model.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: endpoint), let host = url.host,
              url.user == nil, url.password == nil, url.fragment == nil,
              url.scheme == "https" || (url.scheme == "http" && Self.isLocal(host)),
              url.path.hasSuffix("/chat/completions"), !model.isEmpty else {
            throw VideoAnalysisError.message("Open Connection and enter the full chat/completions URL and model name. The endpoint must accept video input.")
        }
        self.url = url
        self.model = model
        self.apiKey = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        if isAlibaba && self.apiKey.isEmpty {
            throw VideoAnalysisError.message("Add your Alibaba Model Studio API key in Connection. The preset uses the Singapore endpoint, so the key must be from that region.")
        }
    }

    private static func isLocal(_ host: String) -> Bool {
        if ["localhost", "::1", "[::1]"].contains(host) || host.hasSuffix(".local") { return true }
        let parts = host.split(separator: ".").compactMap { Int($0) }
        guard parts.count == 4, parts.allSatisfy({ (0...255).contains($0) }) else { return false }
        return parts[0] == 127 || parts[0] == 10 ||
            (parts[0] == 192 && parts[1] == 168) ||
            (parts[0] == 172 && (16...31).contains(parts[1]))
    }
}

enum VideoAnalysisError: LocalizedError {
    case message(String)
    var errorDescription: String? {
        switch self { case .message(let text): return text }
    }
}

struct VideoModelResponse: Codable, Sendable {
    let text: String
    let truncated: Bool
}

/// Native MP4 input over the video_url extension used by Qwen/vLLM-compatible servers.
/// Builds the base64 JSON on disk so a large video does not become several in-memory copies.
struct VideoModelClient: Sendable {
    static let maximumVideoBytes = 100 * 1024 * 1024
    static let prompt = """
    Analyze this video in clear English and address the user's request, including what they ask you to watch for.
    Do not assume a requested event occurs. If no specific focus is provided, describe the visible events broadly.
    Give a brief overall description followed by the important events in chronological order, with approximate timestamps when supported.
    Explain interactions and visible changes, rather than just listing objects. Separate direct observations from interpretations.
    Do not invent actions, causes, ownership, authorization, or intent. State when visibility or the available visual evidence is insufficient.
    Base the answer on the video imagery; do not claim to hear dialogue or sounds. Ordinary activity is a valid answer.
    Return the explanation itself as readable text, not JSON.
    """
    let session: URLSession

    init(session: URLSession? = nil) {
        if let session { self.session = session }
        else {
            let configuration = URLSessionConfiguration.ephemeral
            configuration.timeoutIntervalForRequest = 600
            configuration.timeoutIntervalForResource = 900
            self.session = URLSession(configuration: configuration)
        }
    }

    func describe(videoURL: URL, duration: Double, connection: VideoModelConnection, analysisPrompt: String = "",
                  progress: @escaping @Sendable (Double) -> Void) async throws -> VideoModelResponse {
        try Self.validateDuration(duration, connection: connection)
        let builder = Task.detached(priority: .userInitiated) {
            try Self.makeUploadFile(videoURL: videoURL, duration: duration, model: connection.model,
                                    maximumBytes: connection.maximumVideoBytes, disableThinking: connection.disableThinking,
                                    analysisPrompt: analysisPrompt)
        }
        let body = try await withTaskCancellationHandler {
            try await builder.value
        } onCancel: { builder.cancel() }
        defer { try? FileManager.default.removeItem(at: body) }
        try Task.checkCancellation()

        let request = Self.makeRequest(connection: connection)
        let delegate = VideoUploadDelegate(progress: progress)
        let (data, response) = try await session.upload(for: request, fromFile: body, delegate: delegate)
        try Task.checkCancellation()
        return try Self.decodeCompletion(data, response: response)
    }

    static func validateDuration(_ duration: Double, connection: VideoModelConnection) throws {
        guard duration.isFinite, duration > 0 else {
            throw VideoAnalysisError.message("The video has an invalid duration. Choose another video.")
        }
        if connection.isAlibaba && (duration < 2 || duration > connection.maximumDuration) {
            throw VideoAnalysisError.message("This model accepts videos from 2 seconds to \(Int(connection.maximumDuration / 60)) minutes. Choose a video within that range.")
        }
    }

    static func makeRequest(connection: VideoModelConnection) -> URLRequest {
        var request = URLRequest(url: connection.url)
        request.httpMethod = "POST"
        request.timeoutInterval = 600
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if !connection.apiKey.isEmpty {
            request.setValue("Bearer " + connection.apiKey, forHTTPHeaderField: "Authorization")
        }
        return request
    }

    static func decodeCompletion(_ data: Data, response: URLResponse?) throws -> VideoModelResponse {
        guard let http = response as? HTTPURLResponse else {
            throw VideoAnalysisError.message("The model server returned an invalid response.")
        }
        guard (200..<300).contains(http.statusCode) else {
            let detail: String
            let root = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            let code = (root?["error"] as? [String: Any])?["code"] as? String ?? ""
            switch http.statusCode {
            case 403 where code == "AllocationQuota.FreeTierOnly":
                detail = "This model's free quota has run out or expired. Check its quota in Alibaba Model Studio."
            case 400 where code == "Arrearage", 403 where code == "Arrearage":
                detail = "Alibaba has suspended model access because the account has an overdue balance. Check Model Studio billing."
            case 401, 403: detail = "Check the API key and endpoint access in Connection."
            case 404: detail = "Check the full chat/completions URL and served model name."
            case 400, 422: detail = "Check that the model server accepts MP4 video_url input and has enough video context for this clip."
            case 413: detail = "The server's upload limit is smaller than this video. Try a shorter clip."
            case 429: detail = "The model server is busy or its quota is exhausted. Try again later."
            case 300..<400: detail = "Use the final chat/completions URL; video uploads do not follow redirects."
            default: detail = "The model server could not complete the analysis. Try again or check its logs."
            }
            throw VideoAnalysisError.message("HTTP \(http.statusCode). \(detail)")
        }
        return try Self.decodeResponse(data)
    }

    static func makeUploadFile(videoURL: URL, duration: Double, model: String,
                               maximumBytes: Int = maximumVideoBytes, disableThinking: Bool = false,
                               analysisPrompt: String = "") throws -> URL {
        let attributes = try FileManager.default.attributesOfItem(atPath: videoURL.path)
        guard let size = attributes[.size] as? NSNumber, size.intValue > 0 else {
            throw VideoAnalysisError.message("The prepared video is empty. Choose another video.")
        }
        guard size.intValue <= maximumBytes else {
            let limit = ByteCountFormatter.string(fromByteCount: Int64(maximumBytes), countStyle: .file)
            throw VideoAnalysisError.message("This connection accepts videos up to \(limit) after preparation. Choose a shorter video or select it again after changing the connection.")
        }
        let marker = "VISION_VIDEO_" + UUID().uuidString
        let focus = analysisPrompt.trimmingCharacters(in: .whitespacesAndNewlines)
        let question = focus.isEmpty ? "What happened in this video?" : "Analyze this video for the following request:\n" + focus
        var payload: [String: Any] = [
            "model": model,
            "messages": [
                ["role": "system", "content": prompt],
                ["role": "user", "content": [
                    ["type": "text", "text": question + String(format: "\nThe full clip is approximately %.2f seconds long.", duration)],
                    ["type": "video_url", "video_url": ["url": marker]]
                ]]
            ],
            "max_tokens": 2048, "temperature": 0.2, "stream": false
        ]
        if disableThinking { payload["enable_thinking"] = false }
        let json = String(decoding: try JSONSerialization.data(withJSONObject: payload), as: UTF8.self)
        guard let range = json.range(of: marker) else {
            throw VideoAnalysisError.message("Could not prepare the video request.")
        }
        let output = FileManager.default.temporaryDirectory.appendingPathComponent("vision-request-\(UUID()).json")
        guard FileManager.default.createFile(atPath: output.path, contents: nil) else {
            throw VideoAnalysisError.message("Could not create the upload. Check available storage.")
        }
        do {
            let reader = try FileHandle(forReadingFrom: videoURL)
            defer { try? reader.close() }
            let writer = try FileHandle(forWritingTo: output)
            defer { try? writer.close() }
            try writer.write(contentsOf: Data((String(json[..<range.lowerBound]) + "data:video/mp4;base64,").utf8))
            // A multiple of 3 prevents base64 padding in the middle of the stream.
            var remainder = Data()
            while let chunk = try reader.read(upToCount: 48 * 1024), !chunk.isEmpty {
                try Task.checkCancellation()
                remainder.append(chunk)
                let count = remainder.count - remainder.count % 3
                if count > 0 {
                    try writer.write(contentsOf: Data(remainder.prefix(count).base64EncodedString().utf8))
                    remainder = Data(remainder.dropFirst(count))
                }
            }
            if !remainder.isEmpty { try writer.write(contentsOf: Data(remainder.base64EncodedString().utf8)) }
            try Task.checkCancellation()
            try writer.write(contentsOf: Data(json[range.upperBound...].utf8))
            return output
        } catch {
            try? FileManager.default.removeItem(at: output)
            throw error
        }
    }

    static func decodeResponse(_ data: Data) throws -> VideoModelResponse {
        guard let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let choices = root["choices"] as? [[String: Any]], let choice = choices.first,
              let message = choice["message"] as? [String: Any] else {
            throw VideoAnalysisError.message("Expected a chat completion with a video description. Check the endpoint in Connection.")
        }
        let raw: String
        if let content = message["content"] as? String { raw = content }
        else if let parts = message["content"] as? [[String: Any]] {
            raw = parts.filter { $0["type"] as? String == "text" }.compactMap { $0["text"] as? String }.joined(separator: "\n")
        } else { raw = "" }
        // Some reasoning servers include a think block instead of using reasoning_content.
        var text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.hasPrefix("<think>") {
            guard let end = text.range(of: "</think>") else {
                throw VideoAnalysisError.message("The model produced reasoning but no completed description. Retry or increase the server's output budget.")
            }
            text = String(text[end.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
        }
        guard !text.isEmpty else {
            throw VideoAnalysisError.message("The model returned no description. Check its video support and output budget, then retry.")
        }
        return VideoModelResponse(text: text, truncated: choice["finish_reason"] as? String == "length")
    }
}

private final class VideoUploadDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    let progress: @Sendable (Double) -> Void
    init(progress: @escaping @Sendable (Double) -> Void) { self.progress = progress }
    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64,
                    totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        if totalBytesExpectedToSend > 0 {
            progress(min(1, Double(totalBytesSent) / Double(totalBytesExpectedToSend)))
        }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}
