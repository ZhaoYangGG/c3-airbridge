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
    private(set) var frame: Frame?
    private(set) var running = false
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
        let revision = generation
        let previous = self.stream
        self.stream = nil; running = false
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
        Task { @MainActor in self.event("picker cancelled");self.presenting = false; if !self.running { self.status = "屏幕共享已取消"; self.changed?() } }
    }
    func contentSharingPickerStartDidFailWithError(_ error: Error) { Task { @MainActor in self.failure(error) } }
    func stream(_ stream: SCStream, didStopWithError error: Error) {
        Task { @MainActor in self.event("stream stopped: \(error.localizedDescription)");guard self.stream === stream else { return }; self.stream = nil; self.failure(error) }
    }
    private func failure(_ error: Error) { event("capture failed: \(error.localizedDescription)");presenting = false; running = false; status = error.localizedDescription; stopped?(); changed?() }
    func stop() async {
        generation += 1;changeTask?.cancel();let current = stream; stream = nil; running = false
        try? await current?.stopCapture(); SCContentSharingPicker.shared.isActive = false
        status = "屏幕共享已停止"; stopped?(); changed?()
    }
    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard self.stream === stream, type == .screen, sampleBuffer.isValid,
              Date().timeIntervalSince(processed) >= 0.25,
              let pixels = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        processed = Date()
        autoreleasepool {
            let image = CIImage(cvPixelBuffer: pixels)
            guard let cg = context.createCGImage(image, from: image.extent),
                  let data = context.jpegRepresentation(of: image, colorSpace: CGColorSpaceCreateDeviceRGB(), options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: 0.8]) else { return }
            frame = Frame(jpeg: data, image: cg, at: processed, sequence: (frame?.sequence ?? 0) + 1)
            if UIApplication.shared.applicationState == .background { backgroundFrames += 1 }
            try? data.write(to: PrivateFiles.root.appendingPathComponent("latest.jpg"), options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        }
    }
    func snapshot(after sequence: Int? = nil, timeout: Double = 5) async throws -> Frame {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            try Task.checkCancellation()
            guard running else { throw AgentError.message("屏幕共享未运行") }
            if let frame, Date().timeIntervalSince(frame.at) < 4, sequence == nil || frame.sequence > sequence! { return frame }
            try await Task.sleep(for: .milliseconds(80))
        }
        throw AgentError.message("未收到新画面，任务已停止以避免误操作")
    }
}
