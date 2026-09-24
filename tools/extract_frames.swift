import Foundation
import AVFoundation
import AppKit

let source = URL(fileURLWithPath: CommandLine.arguments[1])
let output = URL(fileURLWithPath: CommandLine.arguments[2])
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
let asset = AVURLAsset(url: source)
let duration = CMTimeGetSeconds(asset.duration)
let generator = AVAssetImageGenerator(asset: asset)
generator.appliesPreferredTrackTransform = true
generator.maximumSize = CGSize(width: 640, height: 640)
generator.requestedTimeToleranceBefore = .zero
generator.requestedTimeToleranceAfter = .zero
var frames: [[String: Any]] = []
for index in 0..<Int(ceil(duration * 2)) {
    let requested = Double(index) / 2
    var actual = CMTime.zero
    let image = try generator.copyCGImage(at: CMTime(seconds: requested, preferredTimescale: 600), actualTime: &actual)
    let bitmap = NSBitmapImageRep(cgImage: image)
    let name = String(format: "frame-%04d.jpg", index)
    try bitmap.representation(using: .jpeg, properties: [.compressionFactor: 0.8])!.write(to: output.appendingPathComponent(name))
    frames.append(["time": CMTimeGetSeconds(actual), "file": name])
}
let manifest: [String: Any] = ["duration": duration, "sampling_fps": 2, "frames": frames]
try JSONSerialization.data(withJSONObject: manifest, options: [.prettyPrinted, .sortedKeys]).write(to: output.appendingPathComponent("frames.json"))
print("Extracted \(frames.count) frames; duration \(duration)s")
