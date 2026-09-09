import Cocoa
import CoreGraphics

let width = 1400
let height = 360
let output = CommandLine.arguments.dropFirst().first ?? "scripts/itougu-momo-watermark.png"
let colorSpace = CGColorSpaceCreateDeviceRGB()
let bitmapInfo = CGImageAlphaInfo.premultipliedLast.rawValue
guard let context = CGContext(data: nil,
                              width: width,
                              height: height,
                              bitsPerComponent: 8,
                              bytesPerRow: width * 4,
                              space: colorSpace,
                              bitmapInfo: bitmapInfo) else {
    fatalError("could not create bitmap context")
}

context.setFillColor(NSColor(calibratedWhite: 0.98, alpha: 1).cgColor)
context.fill(CGRect(x: 0, y: 0, width: width, height: height))

let graphics = NSGraphicsContext(cgContext: context, flipped: false)
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = graphics

let watermarkFont = NSFont(name: "Hiragino Sans GB", size: 28) ?? NSFont.systemFont(ofSize: 28)
let watermarkAttributes: [NSAttributedString.Key: Any] = [
    .font: watermarkFont,
    .foregroundColor: NSColor(calibratedWhite: 0.55, alpha: 0.18),
]
let watermark = "认真一手咸鱼店铺：餐厅焦糖味的momo · 二手转发"

context.saveGState()
context.translateBy(x: -220, y: -80)
context.rotate(by: -0.25)
for y in stride(from: -300, through: height + 500, by: 92) {
    for x in stride(from: -400, through: width + 600, by: 620) {
        (watermark as NSString).draw(at: CGPoint(x: x, y: y), withAttributes: watermarkAttributes)
    }
}
context.restoreGState()

let panel = CGRect(x: 55, y: 72, width: width - 110, height: height - 144)
context.setFillColor(NSColor(calibratedWhite: 1, alpha: 0.96).cgColor)
context.fill(panel)
context.setStrokeColor(NSColor(calibratedRed: 0.82, green: 0.16, blue: 0.12, alpha: 0.78).cgColor)
context.setLineWidth(4)
context.stroke(panel)

let logoFont = NSFont(name: "Avenir Next Demi Bold", size: 70) ?? NSFont.boldSystemFont(ofSize: 70)
let logoAttributes: [NSAttributedString.Key: Any] = [
    .font: logoFont,
    .foregroundColor: NSColor(calibratedRed: 0.82, green: 0.16, blue: 0.12, alpha: 1),
]
("momo" as NSString).draw(at: CGPoint(x: 100, y: 138), withAttributes: logoAttributes)

let detailFont = NSFont(name: "Hiragino Sans GB", size: 28) ?? NSFont.systemFont(ofSize: 28)
let detailAttributes: [NSAttributedString.Key: Any] = [
    .font: detailFont,
    .foregroundColor: NSColor(calibratedWhite: 0.15, alpha: 1),
]
("认真一手咸鱼店铺：餐厅焦糖味的momo" as NSString).draw(at: CGPoint(x: 390, y: 174), withAttributes: detailAttributes)
("此图为专属群归属标识，转发/截图会保留图层" as NSString).draw(at: CGPoint(x: 390, y: 125), withAttributes: detailAttributes)

NSGraphicsContext.restoreGraphicsState()

guard let image = context.makeImage() else { fatalError("could not create image") }
let rep = NSBitmapImageRep(cgImage: image)
guard let png = rep.representation(using: .png, properties: [:]) else { fatalError("could not encode PNG") }
try! png.write(to: URL(fileURLWithPath: output))
