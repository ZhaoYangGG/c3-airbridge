import Foundation
import CoreGraphics

@main struct VisionTests {
    static func image(_ draw: (CGContext)->Void) -> CGImage {
        let context=CGContext(data:nil,width:720,height:1560,bitsPerComponent:8,bytesPerRow:720*4,
                              space:CGColorSpaceCreateDeviceRGB(),bitmapInfo:CGImageAlphaInfo.premultipliedLast.rawValue)!
        context.setFillColor(CGColor(gray:1,alpha:1));context.fill(CGRect(x:0,y:0,width:720,height:1560));draw(context)
        return context.makeImage()!
    }
    static func main() {
        let before=image {_ in}
        let after=image { context in
            context.setFillColor(CGColor(gray:0.6,alpha:1))
            context.fillEllipse(in:CGRect(x:384,y:284,width:32,height:32))
            // A thick gray O-like log glyph previously passed the diff-shape
            // test, causing the real pointer to be steered toward log text.
            context.fillEllipse(in:CGRect(x:204,y:914,width:32,height:32))
            context.setFillColor(CGColor(gray:1,alpha:1));context.fillEllipse(in:CGRect(x:212,y:922,width:16,height:16))
            context.setFillColor(CGColor(gray:0.6,alpha:1));context.fill(CGRect(x:500,y:900,width:32,height:32))
        }
        let found=CursorVision.circles(before,after)
        precondition(found.count==1,"Expected only the solid pointer, got \(found)")
        // Quartz drawing origin is bottom-left for these generated test images.
        precondition(abs(found[0].x-399.5)<2 && abs(found[0].y-1259.5)<2)
        precondition(CursorVision.circles(after,before).isEmpty,"A disappearing pointer must not be treated as a live one")
        print("Passed pointer/text/ghost regression checks")
    }
}
