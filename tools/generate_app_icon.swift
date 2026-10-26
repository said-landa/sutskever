import AppKit

// A simple, opaque eye mark for the development app. Run from the repository root.
let isLab = CommandLine.arguments.dropFirst().contains("--lab")
let appFolder = isLab ? "VisionLab" : "VisionLive"
let target = "ios/\(appFolder)/Assets.xcassets/AppIcon.appiconset/AppIcon.png"
let space = CGColorSpace(name: CGColorSpace.sRGB)!
let context = CGContext(data: nil, width: 1024, height: 1024, bitsPerComponent: 8,
    bytesPerRow: 4096, space: space, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
let navy = CGColor(srgbRed: 0.035, green: 0.075, blue: 0.15, alpha: 1)
context.setFillColor(navy)
context.fill(CGRect(x: 0, y: 0, width: 1024, height: 1024))
context.move(to: CGPoint(x: 160, y: 512))
context.addCurve(to: CGPoint(x: 864, y: 512),
    control1: CGPoint(x: 348, y: 768), control2: CGPoint(x: 676, y: 768))
context.addCurve(to: CGPoint(x: 160, y: 512),
    control1: CGPoint(x: 676, y: 256), control2: CGPoint(x: 348, y: 256))
context.closePath()
context.setLineWidth(56)
context.setLineJoin(.round)
context.setStrokeColor(isLab
    ? CGColor(srgbRed: 1, green: 0.58, blue: 0.18, alpha: 1)
    : CGColor(srgbRed: 0.22, green: 0.86, blue: 0.82, alpha: 1))
context.strokePath()
context.setFillColor(CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 1))
context.fillEllipse(in: CGRect(x: 404, y: 404, width: 216, height: 216))
context.setFillColor(navy)
context.fillEllipse(in: CGRect(x: 462, y: 462, width: 100, height: 100))
let bitmap = NSBitmapImageRep(cgImage: context.makeImage()!)
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: target))
print(target)
