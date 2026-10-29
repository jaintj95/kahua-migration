// Offline text recognition for read-only Kahua screenshots on macOS.
// No screen capture, application control, network requests or record edits.
import Foundation
import ImageIO
import Vision
import CoreGraphics

struct TextLine: Codable {
    let text: String
    let confidence: Float
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

struct Result: Codable {
    let image_width: Int
    let image_height: Int
    let lines: [TextLine]
}

do {
    guard CommandLine.arguments.count == 2 else {
        throw NSError(domain: "ScreenshotOCR", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: "Usage: ocr-screenshot image.png"])
    }
    let url = URL(fileURLWithPath: CommandLine.arguments[1])
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          let original = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        throw NSError(domain: "ScreenshotOCR", code: 2,
                      userInfo: [NSLocalizedDescriptionKey: "Cannot read screenshot"])
    }
    // Small canvas labels and single-digit revision numbers need more pixels.
    // Coordinate output still refers to the unchanged source screenshot.
    guard let bitmap = CGContext(data: nil, width: original.width * 2,
                                 height: original.height * 2, bitsPerComponent: 8,
                                 bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                                 bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
        throw NSError(domain: "ScreenshotOCR", code: 3,
                      userInfo: [NSLocalizedDescriptionKey: "Cannot prepare OCR image"])
    }
    bitmap.interpolationQuality = .high
    bitmap.draw(original, in: CGRect(x: 0, y: 0, width: original.width * 2, height: original.height * 2))
    guard let image = bitmap.makeImage() else {
        throw NSError(domain: "ScreenshotOCR", code: 4,
                      userInfo: [NSLocalizedDescriptionKey: "Cannot prepare OCR image"])
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["en-US"]
    request.usesLanguageCorrection = false
    request.minimumTextHeight = 0.005
    request.preferBackgroundProcessing = true
    try VNImageRequestHandler(cgImage: image).perform([request])
    let lines = (request.results ?? []).compactMap { item -> TextLine? in
        guard let text = item.topCandidates(1).first else { return nil }
        let box = item.boundingBox
        return TextLine(text: text.string, confidence: text.confidence,
                        x: box.minX * Double(original.width),
                        y: (1 - box.maxY) * Double(original.height),
                        width: box.width * Double(original.width),
                        height: box.height * Double(original.height))
    }
    let data = try JSONEncoder().encode(Result(image_width: original.width,
                                             image_height: original.height, lines: lines))
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([10]))
} catch {
    FileHandle.standardError.write(Data((error.localizedDescription + "\n").utf8))
    exit(1)
}
