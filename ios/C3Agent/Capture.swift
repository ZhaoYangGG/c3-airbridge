import Foundation
import UIKit
import ScreenCaptureKit
import CoreImage

struct Frame {
    let jpeg: Data
    let image: CGImage
    let at: Date
    let sequence: Int
    var width: Int { image.width }
    var height: Int { image.height }
}

@MainActor final class Capture: NSObject, @preconcurrency SCContentSharingPickerObserver, @preconcurrency SCStreamDelegate, @preconcurrency SCStreamOutput {
    private var stream: SCStream?
    private var presenting = false
    private var changeTask: Task<Void, Never>?
    private var generation = 0
    private let context = CIContext(options: [.cacheIntermediates: false])
    private var processed = Date.distantPast
    private var sequence = 0
    private var pending: (pixels: CVPixelBuffer, at: Date, generation: Int)?
    private var frameTask: Task<Void,Never>?
    private(set) var frame: Frame?
    private(set) var running = false
    var waitingForSelection: Bool { presenting && !running }
    private(set) var startedAt = Date.distantFuture
    private(set) var status = "等待屏幕共享"
    private(set) var backgroundFrames = 0
    private(set) var events: [[String: Any]] = []
    var changed: (() -> Void)?
    var stopped: (() -> Void)?
    override init() { super.init(); SCContentSharingPicker.shared.add(self) }
    private func event(_ text: String) {
        events.append(["at":Date().timeIntervalSince1970,"text":text])
        if events.count>50 { events.removeFirst() }
    }
    func present() {
        guard !running, !presenting else { return }
        guard UIApplication.shared.applicationState == .active else {
            status="请返回 App 后开始屏幕共享";changed?();return
        }
        presenting = true
        event("picker presented")
        let picker = SCContentSharingPicker.shared
        var config = SCContentSharingPickerConfiguration()
        config.showsMicrophoneControl = false
        picker.defaultConfiguration = config; picker.isActive = true; picker.present()
        status = "请确认共享整个屏幕"; changed?()
    }
    func contentSharingPicker(_ picker: SCContentSharingPicker, didUpdateWith filter: SCContentFilter, for stream: SCStream?) {
        event("filter updated")
        generation += 1
        frameTask?.cancel();frameTask=nil;pending=nil
        let revision = generation
        let previous = self.stream
        self.stream = nil; running = false;frame = nil
        changeTask?.cancel()
        changeTask = Task { @MainActor in
            try? await previous?.stopCapture()
            guard self.generation == revision, !Task.isCancelled else { return }
            do {
                let config = SCStreamConfiguration(); config.width = 720; config.height = 1560
                let capture = SCStream(filter: filter, configuration: config, delegate: self)
                try capture.addStreamOutput(self, type: .screen, sampleHandlerQueue: .main)
                self.stream = capture
                try await capture.startCapture()
                guard self.stream === capture, self.generation == revision, !Task.isCancelled else { try? await capture.stopCapture(); return }
                self.event("capture started")
                self.presenting = false; self.running = true; self.startedAt = Date(); self.status = "原生取屏中"; self.changed?()
            } catch { if self.generation == revision { self.stream = nil; self.failure(error) } }
        }
    }
    func contentSharingPicker(_ picker: SCContentSharingPicker, didCancelFor stream: SCStream?) {
        Task { @MainActor in self.event("picker cancelled");self.presenting = false; if !self.running { self.status = "屏幕共享已取消";self.stopped?(); self.changed?() } }
    }
    func contentSharingPickerStartDidFailWithError(_ error: Error) { Task { @MainActor in self.failure(error) } }
    func stream(_ stream: SCStream, didStopWithError error: Error) {
        Task { @MainActor in self.event("stream stopped: \(error.localizedDescription)");guard self.stream === stream else { return }; self.stream = nil; self.failure(error) }
    }
    private func failure(_ error: Error) { event("capture failed: \(error.localizedDescription)");frameTask?.cancel();frameTask=nil;pending=nil;frame=nil;presenting = false; running = false; status = error.localizedDescription; stopped?(); changed?() }
    func stop() async {
        generation += 1;changeTask?.cancel();let current = stream; stream = nil; running = false;presenting = false;frame = nil
        frameTask?.cancel();frameTask=nil;pending=nil
        try? await current?.stopCapture(); SCContentSharingPicker.shared.isActive = false
        status = "屏幕共享已停止"; stopped?(); changed?()
    }
    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard self.stream === stream, type == .screen, sampleBuffer.isValid,
              let info=(CMSampleBufferGetSampleAttachmentsArray(sampleBuffer,createIfNecessary:false) as? [[SCStreamFrameInfo:Any]])?.first,
              let raw=info[.status] as? Int,raw==SCFrameStatus.complete.rawValue,
              let pixels = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let received=Date()
        let displayed: CMTime
        if let mach=info[.displayTime] as? NSNumber,mach.uint64Value>0 {
            displayed=CMClockMakeHostTimeFromSystemUnits(mach.uint64Value)
        } else {displayed=CMSampleBufferGetPresentationTimeStamp(sampleBuffer)}
        let hostAge=CMTimeGetSeconds(CMTimeSubtract(CMClockGetTime(CMClockGetHostTimeClock()),displayed))
        guard let captured=FrameTiming.capturedAt(received:received,hostAge:hostAge) else{return}
        if let prior=pending?.at ?? frame?.at,captured<=prior {return}
        pending=(pixels,captured,generation)
        guard frameTask==nil else{return}
        let revision=generation
        let delay=max(0,0.25-received.timeIntervalSince(processed))
        // Keep the last frame in a burst. Dropping every callback inside the
        // throttle window used to lose the final pointer position entirely.
        frameTask=Task { @MainActor in
            do {try await Task.sleep(for:.seconds(delay))} catch{return}
            guard self.generation==revision else{return}
            self.processPending();self.frameTask=nil
        }
    }
    private func processPending() {
        guard let pending,pending.generation==generation else{return}
        self.pending=nil;processed=Date()
        autoreleasepool {
            let image = CIImage(cvPixelBuffer: pending.pixels)
            guard let cg = context.createCGImage(image, from: image.extent),
                  let data = context.jpegRepresentation(of: image, colorSpace: CGColorSpaceCreateDeviceRGB(), options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: 0.8]) else { return }
            sequence += 1
            frame = Frame(jpeg: data, image: cg, at: pending.at, sequence: sequence)
            if UIApplication.shared.applicationState == .background { backgroundFrames += 1 }
            try? data.write(to: PrivateFiles.root.appendingPathComponent("latest.jpg"), options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        }
    }
    func snapshot(after sequence: Int? = nil, notBefore: Date? = nil, timeout: Double = 5) async throws -> Frame {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            try Task.checkCancellation()
            guard running else { throw AgentError.message("屏幕共享未运行") }
            if let frame,FrameTiming.accepts(at:frame.at,sequence:frame.sequence,now:Date(),after:sequence,notBefore:notBefore) {return frame}
            try await Task.sleep(for: .milliseconds(80))
        }
        throw AgentError.message("未收到新画面，任务已停止以避免误操作")
    }
}
