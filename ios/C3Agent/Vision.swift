import Foundation
import CoreGraphics

enum CursorVision {
    static func pixels(_ image: CGImage) -> [UInt8]? {
        var data = [UInt8](repeating: 0, count: image.width*image.height*4)
        let ok = data.withUnsafeMutableBytes { bytes -> Bool in
            guard let ctx = CGContext(data: bytes.baseAddress, width: image.width, height: image.height, bitsPerComponent: 8, bytesPerRow: image.width*4,
                                      space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
            ctx.draw(image, in: CGRect(x: 0,y: 0,width: image.width,height: image.height)); return true
        }
        return ok ? data : nil
    }
    static func circles(_ before: CGImage, _ after: CGImage) -> [CGPoint] {
        guard before.width == after.width, before.height == after.height, let a = pixels(before), let b = pixels(after) else { return [] }
        let w = after.width, h = after.height, low = max(6, Int(Double(w)*0.027)), high = Int(Double(w)*0.067)
        var mask = [Bool](repeating: false, count: w*h)
        for p in 0..<w*h { let i=p*4; mask[p] = max(abs(Int(a[i])-Int(b[i])), abs(Int(a[i+1])-Int(b[i+1])), abs(Int(a[i+2])-Int(b[i+2]))) > 15 }
        var result: [CGPoint] = []
        for p in 0..<w*h where mask[p] {
            var todo=[p], head=0, minX=p%w, maxX=p%w, minY=p/w, maxY=p/w
            mask[p]=false
            while head < todo.count {
                let q=todo[head]; head+=1; let x=q%w,y=q/w
                minX=min(minX,x);maxX=max(maxX,x);minY=min(minY,y);maxY=max(maxY,y)
                for next in [x>0 ? q-1 : -1,x<w-1 ? q+1 : -1,y>0 ? q-w : -1,y<h-1 ? q+w : -1] where next >= 0 {
                    if mask[next] { mask[next]=false;todo.append(next) }
                }
            }
            let bw=maxX-minX+1,bh=maxY-minY+1
            if (low...high).contains(bw), (low...high).contains(bh), (0.8...1.2).contains(Double(bw)/Double(bh)),
               (0.55...0.95).contains(Double(todo.count)/Double(bw*bh)) {
                let cx=Double(minX+maxX)/2,cy=Double(minY+maxY)/2,radius=Double(min(bw,bh))*0.27
                var interior=0,gray=0
                var sum=0.0,squared=0.0
                for y in minY...maxY { for x in minX...maxX where hypot(Double(x)-cx,Double(y)-cy)<=radius {
                    let i=(y*w+x)*4,red=Int(b[i]),green=Int(b[i+1]),blue=Int(b[i+2])
                    let value=Double(red+green+blue)/3
                    interior+=1;sum+=value;squared+=value*value
                    if max(red,green,blue)-min(red,green,blue)<16 && value>45 && value<215 { gray+=1 }
                }}
                let mean=sum/Double(max(1,interior))
                let variance=squared/Double(max(1,interior))-mean*mean
                // A newly drawn log glyph can have the same bounding box and
                // diff area as a pointer. The pointer has a solid gray center;
                // text rings/holes and disappearing-pointer ghosts do not.
                if Double(gray)/Double(max(1,interior))>0.82 && variance<230 {
                    result.append(CGPoint(x:cx,y:cy))
                }
            }
        }
        return result
    }
    static func changed(_ before: CGImage,_ after: CGImage,in box: CGRect) -> Double {
        guard let a=before.cropping(to:box),let b=after.cropping(to:box),let x=pixels(a),let y=pixels(b) else{return 0}
        var count=0
        for p in 0..<a.width*a.height {
            let i=p*4
            if max(abs(Int(x[i])-Int(y[i])),abs(Int(x[i+1])-Int(y[i+1])),abs(Int(x[i+2])-Int(y[i+2])))>22 {count+=1}
        }
        return Double(count)/Double(max(1,a.width*a.height))
    }
}
