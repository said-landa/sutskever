import Foundation
import CryptoKit

struct PushConfiguration: Decodable {
    let keyFile: String
    let keyID: String
    let teamID: String
    let registrationFile: String?
}

struct DetectionPush: Decodable {
    let registration: Registration
    let title: String
    let body: String
}

struct Registration: Decodable {
    let deviceToken: String
    let topic: String
    let environment: String
}

enum PushError: Error, LocalizedError {
    case invalid(String)
    var errorDescription: String? {
        switch self { case .invalid(let message): return message }
    }
}

func base64url(_ data: Data) -> String {
    data.base64EncodedString().replacingOccurrences(of: "+", with: "-")
        .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
}

func providerToken(key: P256.Signing.PrivateKey, keyID: String, teamID: String, issuedAt: Int) throws -> String {
    let header = try JSONSerialization.data(withJSONObject: ["alg": "ES256", "kid": keyID], options: [.sortedKeys])
    let claims = try JSONSerialization.data(withJSONObject: ["iss": teamID, "iat": issuedAt], options: [.sortedKeys])
    let input = base64url(header) + "." + base64url(claims)
    let signature = try key.signature(for: Data(input.utf8))
    return input + "." + base64url(signature.rawRepresentation)
}

func payload(title: String, body: String) throws -> Data {
    guard !title.isEmpty, !body.isEmpty else { throw PushError.invalid("Title and body cannot be empty.") }
    let result = try JSONSerialization.data(withJSONObject: ["aps": [
        "alert": ["title": title, "body": body], "sound": "default"]])
    guard result.count <= 4096 else { throw PushError.invalid("Payload exceeds APNs' 4096-byte limit.") }
    return result
}

func validated(_ registration: Registration) throws -> Registration {
    guard !registration.deviceToken.isEmpty, registration.deviceToken.count.isMultiple(of: 2),
          registration.deviceToken.unicodeScalars.allSatisfy({ "0123456789abcdefABCDEF".unicodeScalars.contains($0) }) else {
        throw PushError.invalid("Invalid device token: expected hexadecimal bytes.")
    }
    guard registration.topic == "com.vision.lab" else { throw PushError.invalid("This test sender targets Vision Lab only.") }
    guard ["sandbox", "production"].contains(registration.environment) else { throw PushError.invalid("Expected sandbox or production registration.") }
    return registration
}

func selfTest() throws {
    let key = P256.Signing.PrivateKey()
    let token = try providerToken(key: key, keyID: "TESTKEY", teamID: "TESTTEAM", issuedAt: 123456)
    let parts = token.split(separator: ".").map(String.init)
    func decode(_ value: String) -> Data {
        let standard = value.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        return Data(base64Encoded: standard + String(repeating: "=", count: (4 - standard.count % 4) % 4))!
    }
    guard parts.count == 3 else { throw PushError.invalid("JWT structure failed.") }
    let signature = try P256.Signing.ECDSASignature(rawRepresentation: decode(parts[2]))
    guard key.publicKey.isValidSignature(signature, for: Data((parts[0] + "." + parts[1]).utf8)) else {
        throw PushError.invalid("ES256 signature verification failed.")
    }
    let claims = try JSONSerialization.jsonObject(with: decode(parts[1])) as! [String: Any]
    guard claims["iss"] as? String == "TESTTEAM", claims["iat"] as? Int == 123456 else { throw PushError.invalid("JWT claims failed.") }
    let small = try payload(title: "Vision Lab", body: "Test")
    let root = try JSONSerialization.jsonObject(with: small) as! [String: Any]
    guard let aps = root["aps"] as? [String: Any], aps["sound"] as? String == "default" else { throw PushError.invalid("Alert payload failed.") }
    do {
        _ = try payload(title: "Test", body: String(repeating: "x", count: 5000))
        throw PushError.invalid("Oversized payload was accepted.")
    } catch PushError.invalid(let reason) where reason.contains("4096") {}
    do {
        _ = try validated(Registration(deviceToken: "00", topic: "com.vision.live", environment: "sandbox"))
        throw PushError.invalid("Main-app topic was accepted.")
    } catch PushError.invalid(let reason) where reason.contains("Lab only") {}
    _ = try validated(Registration(deviceToken: "00", topic: "com.vision.lab", environment: "production"))
    print("PASS: ES256 signature, JWT claims, alert payload, size limit, and Lab isolation. No push sent.")
}

@main
struct PushSender {
    static func main() async {
        do {
            var arguments = Array(CommandLine.arguments.dropFirst())
            let detectionMode = arguments.first == "--event-stdin"
            if detectionMode { arguments.removeFirst() }
            if arguments == ["--self-test"] { try selfTest(); return }
            guard arguments.count == 2, ["--config", "--check-config"].contains(arguments[0]) else {
                print("Usage: send_push --config /absolute/path/push-config.json\n       send_push --check-config /absolute/path/push-config.json\n       send_push --self-test")
                exit(arguments.isEmpty ? 0 : 2)
            }
            let config = try JSONDecoder().decode(PushConfiguration.self,
                from: Data(contentsOf: URL(fileURLWithPath: arguments[1])))
            guard !config.keyID.isEmpty, !config.teamID.isEmpty,
                  config.keyFile.hasPrefix("/") else {
                throw PushError.invalid("Provide Key ID, Team ID, and absolute local file paths.")
            }
            let key = try P256.Signing.PrivateKey(pemRepresentation: String(contentsOfFile: config.keyFile, encoding: .utf8))
            if arguments[0] == "--check-config" {
                _ = try providerToken(key: key, keyID: config.keyID, teamID: config.teamID,
                    issuedAt: Int(Date().timeIntervalSince1970))
                print("Configuration and P-256 signing key validated locally. No push sent; Apple key scope is not verified yet.")
                return
            }
            let detection: DetectionPush?
            let registration: Registration
            if detectionMode {
                var input = Data()
                while let chunk = try FileHandle.standardInput.read(upToCount: min(1024, 8193 - input.count)), !chunk.isEmpty {
                    input.append(chunk)
                    guard input.count <= 8192 else { throw PushError.invalid("Detection input exceeds size limit.") }
                }
                let event = try JSONDecoder().decode(DetectionPush.self, from: input)
                detection = event
                registration = try validated(event.registration)
            } else {
                detection = nil
                guard let file = config.registrationFile, file.hasPrefix("/") else { throw PushError.invalid("Provide an absolute registrationFile for a test push.") }
                registration = try validated(JSONDecoder().decode(Registration.self,
                    from: Data(contentsOf: URL(fileURLWithPath: file))))
            }
            let jwt = try providerToken(key: key, keyID: config.keyID, teamID: config.teamID,
                issuedAt: Int(Date().timeIntervalSince1970))
            let data = try payload(title: detection?.title ?? "Vision Lab · remote push test",
                body: detection?.body ?? "Sent by your Mac through Apple. This is a delivery test, not a model detection.")
            let requestID = UUID().uuidString.lowercased()
            // Pass authentication through stdin, not process arguments or logs.
            let curlConfig = """
            url = "https://\(registration.environment == "production" ? "api.push.apple.com" : "api.sandbox.push.apple.com")/3/device/\(registration.deviceToken)"
            header = "authorization: bearer \(jwt)"
            header = "apns-topic: \(registration.topic)"
            header = "apns-push-type: alert"
            header = "apns-priority: 10"
            header = "apns-expiration: 0"
            header = "apns-id: \(requestID)"
            header = "content-type: application/json"
            """
            let temporary = FileManager.default.temporaryDirectory.appendingPathComponent("vision-push-" + UUID().uuidString)
            try FileManager.default.createDirectory(at: temporary, withIntermediateDirectories: false,
                attributes: [.posixPermissions: 0o700])
            defer { try? FileManager.default.removeItem(at: temporary) }
            let payloadURL = temporary.appendingPathComponent("payload.json")
            let responseURL = temporary.appendingPathComponent("response.json")
            try data.write(to: payloadURL)
            let input = Pipe(), output = Pipe(), errors = Pipe()
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/curl")
            process.arguments = ["--http2", "--silent", "--show-error", "--max-time", "20", "--config", "-",
                "--request", "POST", "--data-binary", "@" + payloadURL.path, "--output", responseURL.path,
                "--write-out", "%{http_code} %{http_version}"]
            process.standardInput = input; process.standardOutput = output; process.standardError = errors
            try process.run()
            input.fileHandleForWriting.write(Data(curlConfig.utf8))
            try input.fileHandleForWriting.close()
            let summary = output.fileHandleForReading.readDataToEndOfFile()
            let errorData = errors.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            guard process.terminationStatus == 0 else {
                // Do not print curl's error text: it can contain request URLs/device tokens.
                _ = errorData
                throw PushError.invalid("Connection to APNs failed (curl exit \(process.terminationStatus)). No delivery confirmed.")
            }
            let status = String(decoding: summary, as: UTF8.self).split(separator: " ")
            guard status.count == 2, status[1] == "2" else { throw PushError.invalid("APNs connection did not negotiate HTTP/2.") }
            guard status[0] == "200" else {
                let response = (try? Data(contentsOf: responseURL)) ?? Data()
                let object = (try? JSONSerialization.jsonObject(with: response)) as? [String: Any]
                let reason = object?["reason"] as? String ?? "Unknown"
                throw PushError.invalid("APNs rejected the push: HTTP \(status[0]), \(reason).")
            }
            print("APNs accepted the push (HTTP 200 / HTTP 2). Request: \(requestID)")
            print("Check the locked iPhone. Acceptance is not proof that the banner appeared.")
        } catch {
            fputs("Push failed: \(error.localizedDescription)\n", stderr)
            exit(1)
        }
    }
}
