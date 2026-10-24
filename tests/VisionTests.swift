import Foundation
import CoreGraphics
import CoreText

@main struct VisionTests {
    static func image(_ draw: (CGContext)->Void) -> CGImage {
        let context=CGContext(data:nil,width:720,height:1560,bitsPerComponent:8,bytesPerRow:720*4,
                              space:CGColorSpaceCreateDeviceRGB(),bitmapInfo:CGImageAlphaInfo.premultipliedLast.rawValue)!
        context.setFillColor(CGColor(gray:1,alpha:1));context.fill(CGRect(x:0,y:0,width:720,height:1560));draw(context)
        return context.makeImage()!
    }
    static func main() throws {
        let now=Date(timeIntervalSince1970:1000),barrier=Date(timeIntervalSince1970:999.8)
        let queued=FrameTiming.capturedAt(received:now,hostAge:0.5)!
        precondition(!FrameTiming.accepts(at:queued,sequence:20,now:now,after:19,notBefore:barrier),"A queued pre-move frame must be rejected even if its callback/sequence is new")
        let fresh=FrameTiming.capturedAt(received:now,hostAge:0.1)!
        precondition(FrameTiming.accepts(at:fresh,sequence:20,now:now,after:19,notBefore:barrier))
        precondition(!FrameTiming.accepts(at:fresh,sequence:19,now:now,after:19,notBefore:barrier))
        precondition(FrameTiming.capturedAt(received:now,hostAge:Double.nan)==nil)
        precondition(FrameTiming.capturedAt(received:now,hostAge:-1)==nil)
        precondition(FrameTiming.capturedAt(received:now,hostAge:8)==nil)
        for delta in [CGPoint(x:60,y:0),CGPoint(x:-40,y:25),CGPoint(x:0,y:-0.1)] {
            let (x,y)=MouseStep.units(dx:delta.x,dy:delta.y,width:720,acceleration:0.5,reversed:false)
            precondition(hypot(Double(x),Double(y))<=6.5)
            precondition(Double(x)*delta.x+Double(y)*delta.y>0,"Correction must point toward target, including vertical-only small errors")
            let (rx,ry)=MouseStep.units(dx:delta.x,dy:delta.y,width:720,acceleration:0.5,reversed:true)
            precondition(hypot(Double(rx),Double(ry))<=3.7,"Overshoot reversal must slow down")
        }
        // A deliberately miscalibrated plant triples its gain mid-approach.
        // The fine controller must recover after overshoot without widening
        // the click tolerance or assuming the predicted position was observed.
        var point=CGPoint(x:500,y:700),previous=CGVector.zero
        let target=CGPoint(x:550,y:750)
        for step in 0..<48 {
            let dx=target.x-point.x,dy=target.y-point.y
            if hypot(dx,dy)<17 {break}
            let reversed=dx*previous.dx+dy*previous.dy<0
            let (x,y)=MouseStep.units(dx:dx,dy:dy,width:720,acceleration:0.5,reversed:reversed)
            previous=CGVector(dx:dx,dy:dy)
            let measuredGain = step<2 ? 2.0 : 6.0
            point.x+=Double(x)*measuredGain;point.y+=Double(y)*measuredGain
        }
        precondition(hypot(target.x-point.x,target.y-point.y)<17,"Fine positioning must converge after a gain jump")
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
        func display(_ text: String) -> CGImage {
            image { context in
                let string=NSAttributedString(string:text,attributes:[kCTFontAttributeName as NSAttributedString.Key:CTFontCreateWithName("Helvetica" as CFString,120,nil),kCTForegroundColorAttributeName as NSAttributedString.Key:CGColor(gray:0,alpha:1)])
                context.textPosition=CGPoint(x:80,y:720)
                CTLineDraw(CTLineCreateWithAttributedString(string),context)
            }
        }
        // Match the display-only crop used by the app, excluding buttons and
        // status bar. Keep the entire display width, not just the leading 0.
        let full=CGRect(x:0,y:0.42,width:1,height:0.2)
        precondition(ScreenText.calculatorReading(["0"]) == .zero)
        precondition(ScreenText.calculatorReading([]) == .unreadable,"Missing OCR must require a separate display reading, never assume zero")
        precondition(ScreenText.calculatorReading(["<uncertain>"]) == .unreadable)
        precondition(ScreenText.calculatorReading(["0","<uncertain>"]) == .unreadable)
        precondition(ScreenText.calculatorReading(["0.27","<uncertain>"]) == .occupied)
        precondition(ScreenText.calculatorReading(["0","0"]) == .occupied)
        // A single glyph is sometimes not detected by Vision at all.
        let zero=ScreenText.calculatorReading(try ScreenText.read(display("0"),normalized:full))
        precondition(zero == .zero || zero == .unreadable)
        for text in ["0.27","0.","27+58"] {
            precondition(ScreenText.calculatorReading([text]) == .occupied)
            let reading=ScreenText.calculatorReading(try ScreenText.read(display(text),normalized:full))
            precondition(reading != .zero,"Local OCR must not accept stale calculator value \(text)")
        }
        print("Passed frame freshness, fine-control, pointer/text/ghost and calculator OCR regression checks")
    }
}
