// Native microphone -> 16 kHz mono signed little-endian PCM16 on stdout.
// stderr contains operational JSON only. No playback, network, or transcript.
import AVFoundation
import AudioToolbox
import CoreAudio
import CoreMedia
import Darwin
import Foundation

enum CaptureError: Error, CustomStringConvertible {
    case message(String)
    var description: String { if case .message(let value) = self { return value }; return "capture error" }
}

func diagnostic(_ value: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) {
        FileHandle.standardError.write(data)
        FileHandle.standardError.write(Data([10]))
    }
}

func propertyAddress(_ selector: AudioObjectPropertySelector,
                     _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
}

func deviceString(_ device: AudioDeviceID, _ selector: AudioObjectPropertySelector) -> String {
    var address = propertyAddress(selector)
    let value = UnsafeMutablePointer<Unmanaged<CFString>?>.allocate(capacity: 1)
    value.initialize(to: nil)
    defer { value.deinitialize(count: 1); value.deallocate() }
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    guard AudioObjectGetPropertyData(device, &address, 0, nil, &size, value) == noErr,
          let string = value.pointee else { return "" }
    return string.takeRetainedValue() as String
}

func devices() throws -> [[String: Any]] {
    var address = propertyAddress(kAudioHardwarePropertyDevices)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size) == noErr else {
        throw CaptureError.message("Cannot enumerate CoreAudio devices")
    }
    var identifiers = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
    let status = identifiers.withUnsafeMutableBytes {
        AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, $0.baseAddress!)
    }
    guard status == noErr else { throw CaptureError.message("Cannot read CoreAudio device list") }
    var output: [[String: Any]] = []
    for device in identifiers {
        var streams = propertyAddress(kAudioDevicePropertyStreams, kAudioDevicePropertyScopeInput)
        var bytes: UInt32 = 0
        if AudioObjectGetPropertyDataSize(device, &streams, 0, nil, &bytes) == noErr && bytes > 0 {
            output.append(["id": "coreaudio:\(device)",
                           "name": deviceString(device, kAudioObjectPropertyName),
                           "uid": deviceString(device, kAudioDevicePropertyDeviceUID)])
        }
    }
    return output
}

func selectedDevice(_ argument: String) throws -> AudioDeviceID {
    if argument == "default" {
        var address = propertyAddress(kAudioHardwarePropertyDefaultInputDevice)
        var device: AudioDeviceID = 0
        var size = UInt32(MemoryLayout<AudioDeviceID>.size)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &device) == noErr,
              device != 0 else { throw CaptureError.message("No default input device") }
        return device
    }
    let choices = try devices()
    guard let chosen = choices.first(where: { $0["id"] as? String == argument || $0["name"] as? String == argument || $0["uid"] as? String == argument }),
          let identifier = chosen["id"] as? String,
          let number = UInt32(identifier.dropFirst("coreaudio:".count)) else {
        throw CaptureError.message("Input device is unavailable; select a CoreAudio ID from --list-devices")
    }
    return number
}

final class PCMConverter {
    let converter: AVAudioConverter
    let outputFormat: AVAudioFormat
    let inputRate: Double
    private(set) var frames: Int64 = 0
    private var inputFrames: Int64 = 0

    init(inputFormat: AVAudioFormat) throws {
        inputRate = inputFormat.sampleRate
        guard let format = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000,
                                        channels: 1, interleaved: true),
              let instance = AVAudioConverter(from: inputFormat, to: format) else {
            throw CaptureError.message("Cannot initialize PCM conversion")
        }
        outputFormat = format
        converter = instance
        converter.primeMethod = .none
        converter.sampleRateConverterQuality = AVAudioQuality.max.rawValue
    }

    func write(_ data: Data) throws {
        try data.withUnsafeBytes { bytes in
            var remaining = bytes.count
            var offset = 0
            while remaining > 0 {
                let count = Darwin.write(STDOUT_FILENO, bytes.baseAddress!.advanced(by: offset), remaining)
                if count < 0 {
                    if errno == EINTR { continue }
                    throw CaptureError.message("PCM output pipe write failed (errno \(errno))")
                }
                if count == 0 { throw CaptureError.message("PCM output pipe made no progress") }
                remaining -= count
                offset += count
            }
        }
    }

    func convert(_ input: AVAudioPCMBuffer?, end: Bool = false) throws {
        inputFrames += Int64(input?.frameLength ?? 0)
        var supplied = false
        let capacity = AVAudioFrameCount(max(2048, Int(ceil(Double(input?.frameLength ?? 0) * 16000 / inputRate)) + 256))
        for _ in 0..<100 {
            guard let output = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity) else {
                throw CaptureError.message("Cannot allocate converted buffer")
            }
            var failure: NSError?
            let status = converter.convert(to: output, error: &failure) { _, inputStatus in
                if let source = input, !supplied {
                    supplied = true
                    inputStatus.pointee = .haveData
                    return source
                }
                inputStatus.pointee = end ? .endOfStream : .noDataNow
                return nil
            }
            if let failure = failure { throw failure }
            if status == .error { throw CaptureError.message("PCM conversion failed") }
            if output.frameLength > 0 {
                let data = output.audioBufferList.pointee.mBuffers
                guard let pointer = data.mData else { throw CaptureError.message("Empty converted PCM pointer") }
                // AVAudioConverter drains a short resampler filter tail at EOF.
                // Keep only the duration supported by real input samples.
                let target = Int64((Double(inputFrames) * 16000 / inputRate).rounded())
                let amount = end ? min(Int64(output.frameLength), max(0, target - frames)) : Int64(output.frameLength)
                try write(Data(bytes: pointer, count: Int(amount) * 2))
                frames += amount
            }
            if status == .inputRanDry || status == .endOfStream { return }
            if output.frameLength == 0 { return }
        }
        throw CaptureError.message("PCM conversion failed to drain")
    }
}

final class BufferQueue {
    private let condition = NSCondition()
    private var buffers: [AVAudioPCMBuffer] = []
    private var accepting = true
    private var stopRequested = false
    private var expectedSampleTime: AVAudioFramePosition?
    private var inputFrames: Int64 = 0
    private var outputFrames: Int64 = 0
    private var overflows = 0
    private var discontinuities = 0
    private var missingFrames: Int64 = 0
    private var peakBuffers = 0
    private var firstHost: Double?
    private var lastHostEnd: Double?
    private var failure: String?
    private var inputRate: Double?
    private var inputChannels: UInt32?
    let capacity = 128

    func requestStop() { condition.lock(); stopRequested = true; condition.broadcast(); condition.unlock() }
    func shouldStop() -> Bool {
        condition.lock(); defer { condition.unlock() }
        return stopRequested || failure != nil
    }
    func fail(_ message: String) { condition.lock(); failure = failure ?? message; condition.broadcast(); condition.unlock() }
    // Caller transfers its newly copied buffer; it must not modify it afterward.
    func append(_ input: AVAudioPCMBuffer, sampleTime: Int64, hostSeconds: Double) {
        condition.lock(); defer { condition.unlock() }
        if !accepting { return }
        // CMTime -> native-frame rounding may differ by one frame. Larger gaps
        // are data loss, including drops upstream of our bounded queue.
        if let expected = expectedSampleTime, abs(sampleTime - expected) > 1 {
            discontinuities += 1
            missingFrames += max(0, sampleTime - expected)
            failure = failure ?? "Input sample clock discontinuity"
        }
        expectedSampleTime = sampleTime + Int64(input.frameLength)
        inputRate = input.format.sampleRate
        inputChannels = input.format.channelCount
        firstHost = firstHost ?? hostSeconds
        lastHostEnd = hostSeconds + Double(input.frameLength) / input.format.sampleRate
        inputFrames += Int64(input.frameLength)
        if buffers.count >= capacity {
            overflows += 1
            failure = failure ?? "Capture queue overflow; input audio was lost"
            return
        }
        buffers.append(input)
        peakBuffers = max(peakBuffers, buffers.count)
        condition.signal()
    }

    func next() -> AVAudioPCMBuffer? {
        condition.lock(); defer { condition.unlock() }
        while buffers.isEmpty && accepting { condition.wait() }
        return buffers.isEmpty ? nil : buffers.removeFirst()
    }
    func finish() { condition.lock(); accepting = false; condition.broadcast(); condition.unlock() }
    func converted(_ count: Int64) { condition.lock(); outputFrames = count; condition.unlock() }
    func stats() -> [String: Any] {
        condition.lock(); defer { condition.unlock() }
        var result: [String: Any] = ["input_frames": inputFrames, "output_frames": outputFrames,
            "queue_buffers": buffers.count, "queue_peak_buffers": peakBuffers,
            "queue_capacity_buffers": capacity, "overflows": overflows,
            "sample_time_discontinuities": discontinuities, "missing_input_frames": missingFrames]
        if let rate = inputRate { result["input_rate"] = rate }
        if let channels = inputChannels { result["input_channels"] = channels }
        if let first = firstHost, let end = lastHostEnd { result["input_host_span_seconds"] = end - first }
        if let error = failure { result["error"] = error }
        return result
    }
}

final class AudioReceiver: NSObject, AVCaptureAudioDataOutputSampleBufferDelegate {
    let queue: BufferQueue
    private var firstFormat: AVAudioFormat?
    init(queue: BufferQueue) { self.queue = queue }

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        consume(sampleBuffer)
    }

    func consume(_ sampleBuffer: CMSampleBuffer) {
        // This serial callback only copies PCM and enqueues it. Conversion and
        // stdout writes happen elsewhere so slow storage cannot block delivery.
        guard CMSampleBufferDataIsReady(sampleBuffer),
              let description = CMSampleBufferGetFormatDescription(sampleBuffer),
              let stream = CMAudioFormatDescriptionGetStreamBasicDescription(description),
              stream.pointee.mFormatID == kAudioFormatLinearPCM,
              let format = AVAudioFormat(streamDescription: stream),
              format.sampleRate > 0, format.channelCount > 0 else {
            queue.fail("Capture delivered an unsupported audio format"); return
        }
        if let initial = firstFormat, !initial.isEqual(format) {
            queue.fail("Input PCM format changed during capture"); return
        }
        firstFormat = format
        let count = CMSampleBufferGetNumSamples(sampleBuffer)
        guard count > 0, count <= Int(Int32.max),
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(count)) else {
            queue.fail("Cannot allocate captured PCM buffer"); return
        }
        buffer.frameLength = AVAudioFrameCount(count)
        let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(sampleBuffer, at: 0,
            frameCount: Int32(count), into: buffer.mutableAudioBufferList)
        guard status == noErr else {
            queue.fail("Cannot copy captured PCM (OSStatus \(status))"); return
        }
        let timestamp = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
        let seconds = CMTimeGetSeconds(timestamp)
        guard timestamp.isNumeric, seconds.isFinite, seconds >= 0,
              seconds * format.sampleRate < Double(Int64.max) else {
            queue.fail("Input PCM has no usable presentation timestamp"); return
        }
        queue.append(buffer, sampleTime: Int64((seconds * format.sampleRate).rounded()), hostSeconds: seconds)
    }
}

func sampleBufferForTest(_ input: AVAudioPCMBuffer, sampleTime: Int64) throws -> CMSampleBuffer {
    var description: CMAudioFormatDescription?
    let formatStatus = CMAudioFormatDescriptionCreate(allocator: kCFAllocatorDefault,
        asbd: input.format.streamDescription, layoutSize: 0, layout: nil, magicCookieSize: 0,
        magicCookie: nil, extensions: nil, formatDescriptionOut: &description)
    guard formatStatus == noErr, let description = description else {
        throw CaptureError.message("Synthetic audio format creation failed")
    }
    var timing = CMSampleTimingInfo(duration: CMTime(value: 1, timescale: Int32(input.format.sampleRate)),
        presentationTimeStamp: CMTime(value: sampleTime, timescale: Int32(input.format.sampleRate)),
        decodeTimeStamp: .invalid)
    var sample: CMSampleBuffer?
    let createStatus = CMSampleBufferCreate(allocator: kCFAllocatorDefault, dataBuffer: nil,
        dataReady: false, makeDataReadyCallback: nil, refcon: nil, formatDescription: description,
        sampleCount: Int(input.frameLength), sampleTimingEntryCount: 1, sampleTimingArray: &timing,
        sampleSizeEntryCount: 0, sampleSizeArray: nil, sampleBufferOut: &sample)
    guard createStatus == noErr, let sample = sample else {
        throw CaptureError.message("Synthetic sample buffer creation failed")
    }
    let copyStatus = CMSampleBufferSetDataBufferFromAudioBufferList(sample,
        blockBufferAllocator: kCFAllocatorDefault, blockBufferMemoryAllocator: kCFAllocatorDefault,
        flags: 0, bufferList: input.audioBufferList)
    guard copyStatus == noErr, CMSampleBufferSetDataReady(sample) == noErr else {
        throw CaptureError.message("Synthetic sample buffer data copy failed")
    }
    return sample
}

func selfTest(rate: Double, seconds: Double, pipeline: Bool = false) throws {
    guard rate >= 16000, rate <= 192000, seconds > 0, seconds <= 10,
          let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate,
                                     channels: 2, interleaved: false) else {
        throw CaptureError.message("Invalid synthetic test settings")
    }
    let converter = try PCMConverter(inputFormat: format)
    let queue = BufferQueue()
    let receiver = AudioReceiver(queue: queue)
    let total = Int((rate * seconds).rounded())
    var processed = 0
    while processed < total {
        let amount = min(4096, total - processed)
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(amount))!
        buffer.frameLength = AVAudioFrameCount(amount)
        for channel in 0..<2 {
            for index in 0..<amount {
                buffer.floatChannelData![channel][index] = Float(0.1 * sin(2 * .pi * 440 * Double(processed + index) / rate))
            }
        }
        if pipeline {
            receiver.consume(try sampleBufferForTest(buffer, sampleTime: Int64(rate * 123456) + Int64(processed)))
            if queue.shouldStop() { throw CaptureError.message("Synthetic pipeline failed: \(queue.stats())") }
            // consume enqueues synchronously; no microphone or capture session.
            try converter.convert(queue.next()!)
            queue.converted(converter.frames)
        } else {
            try converter.convert(buffer)
        }
        processed += amount
    }
    try converter.convert(nil, end: true)
    queue.finish()
    diagnostic(["event": "self_test", "input_rate": rate, "input_frames": total,
                "output_frames": converter.frames, "output_rate": 16000,
                "pipeline": pipeline, "queue": queue.stats()])
}

func selfTestQueue() throws {
    let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 48000, channels: 1, interleaved: false)!
    let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 512)!
    buffer.frameLength = 512
    let discontinuous = BufferQueue()
    discontinuous.append(buffer, sampleTime: 0, hostSeconds: 100)
    discontinuous.append(buffer, sampleTime: 517, hostSeconds: 100 + 517.0 / 48000)
    guard discontinuous.shouldStop(), discontinuous.stats()["missing_input_frames"] as? Int64 == 5 else {
        throw CaptureError.message("Synthetic input gap was not detected")
    }
    discontinuous.finish()
    guard discontinuous.next() != nil, discontinuous.next() != nil, discontinuous.next() == nil else {
        throw CaptureError.message("Queued PCM was not retained after detected input gap")
    }
    let overflow = BufferQueue()
    for index in 0...overflow.capacity {
        overflow.append(buffer, sampleTime: Int64(index * 512), hostSeconds: 100 + Double(index * 512) / 48000)
    }
    guard overflow.shouldStop(), overflow.stats()["overflows"] as? Int == 1 else {
        throw CaptureError.message("Synthetic bounded queue overflow was not detected")
    }
    overflow.finish()
    var retained = 0
    while overflow.next() != nil { retained += 1 }
    guard retained == overflow.capacity else { throw CaptureError.message("Queue overflow changed retained PCM") }
    diagnostic(["event": "self_test_queue", "discontinuity": discontinuous.stats(), "overflow": overflow.stats()])
}

func capture(device argument: String) throws -> Int32 {
    let identifier = try selectedDevice(argument)
    let authorization = AVCaptureDevice.authorizationStatus(for: .audio)
    if authorization == .denied || authorization == .restricted {
        throw CaptureError.message("Microphone permission is denied; enable it in macOS Privacy settings")
    }
    if authorization == .notDetermined {
        let ready = DispatchSemaphore(value: 0)
        var allowed = false
        AVCaptureDevice.requestAccess(for: .audio) { value in allowed = value; ready.signal() }
        guard ready.wait(timeout: .now() + 30) == .success, allowed else {
            throw CaptureError.message("Microphone permission was not granted")
        }
    }
    let uid = deviceString(identifier, kAudioDevicePropertyDeviceUID)
    guard !uid.isEmpty, let device = AVCaptureDevice(uniqueID: uid), device.hasMediaType(.audio) else {
        throw CaptureError.message("The selected CoreAudio device is unavailable to AVCaptureSession")
    }
    let session = AVCaptureSession()
    let input = try AVCaptureDeviceInput(device: device)
    let output = AVCaptureAudioDataOutput()
    let queue = BufferQueue()
    let receiver = AudioReceiver(queue: queue)
    let callbacks = DispatchQueue(label: "lecture-audio-callback", qos: .userInteractive)
    output.setSampleBufferDelegate(receiver, queue: callbacks)
    // The default nil audioSettings delivers the device's native PCM format.
    // This explicit input is independent of changes to the OS default device.
    session.beginConfiguration()
    guard session.canAddInput(input), session.canAddOutput(output) else {
        session.commitConfiguration()
        throw CaptureError.message("Cannot attach the selected microphone to the capture session")
    }
    session.addInput(input)
    session.addOutput(output)
    session.commitConfiguration()
    diagnostic(["event": "configured", "backend": "AVCaptureSession", "device": "coreaudio:\(identifier)",
                "device_name": device.localizedName, "device_uid": device.uniqueID])
    let drained = DispatchSemaphore(value: 0)
    let writer = Thread {
        do {
            var converter: PCMConverter?
            while let buffer = queue.next() {
                if converter == nil { converter = try PCMConverter(inputFormat: buffer.format) }
                try converter!.convert(buffer)
                queue.converted(converter!.frames)
            }
            if let converter = converter {
                try converter.convert(nil, end: true)
                queue.converted(converter.frames)
            }
        } catch { queue.fail(String(describing: error)) }
        drained.signal()
    }
    writer.name = "lecture-pcm-writer"
    writer.start()
    Darwin.signal(SIGPIPE, SIG_IGN)
    Darwin.signal(SIGINT, SIG_IGN)
    Darwin.signal(SIGTERM, SIG_IGN)
    let interrupts = [SIGINT, SIGTERM].map { number -> DispatchSourceSignal in
        let source = DispatchSource.makeSignalSource(signal: number, queue: DispatchQueue.global())
        source.setEventHandler { queue.requestStop() }
        source.resume()
        return source
    }
    let center = NotificationCenter.default
    let observers = [
        center.addObserver(forName: AVCaptureSession.runtimeErrorNotification, object: session, queue: nil) { notification in
            let error = notification.userInfo?[AVCaptureSessionErrorKey] as? NSError
            queue.fail("Capture session error: \(error?.localizedDescription ?? "unknown runtime error")")
        },
        center.addObserver(forName: AVCaptureSession.wasInterruptedNotification, object: session, queue: nil) { _ in
            queue.fail("Capture session was interrupted")
        },
        center.addObserver(forName: AVCaptureDevice.wasDisconnectedNotification, object: device, queue: nil) { _ in
            queue.fail("Selected input device was disconnected")
        }
    ]
    let activity = ProcessInfo.processInfo.beginActivity(options: [.idleSystemSleepDisabled], reason: "講演音声を保存中")
    defer {
        ProcessInfo.processInfo.endActivity(activity)
        observers.forEach { center.removeObserver($0) }
        interrupts.forEach { $0.cancel() }
    }
    session.startRunning()
    if !session.isRunning { queue.fail("Capture session did not start") }
    diagnostic(["event": "started", "backend": "AVCaptureSession", "device": "coreaudio:\(identifier)",
                "device_name": device.localizedName, "output_rate": 16000, "output_channels": 1,
                "sleep_activity": "idleSystemSleepDisabled", "session_running": session.isRunning])
    var nextReport = Date().addingTimeInterval(1)
    while !queue.shouldStop() {
        RunLoop.current.run(until: Date().addingTimeInterval(0.05))
        if !session.isRunning { queue.fail("Capture session stopped unexpectedly") }
        if !device.isConnected { queue.fail("Selected input device is no longer connected") }
        if Date() >= nextReport {
            diagnostic(queue.stats().merging(["event": "progress"]) { _, new in new })
            nextReport = Date().addingTimeInterval(1)
        }
    }
    // Stop hardware delivery, finish all already submitted callbacks, then drain
    // conversion. Do not finish the queue before the final callback is copied.
    session.stopRunning()
    callbacks.sync {}
    output.setSampleBufferDelegate(nil, queue: nil)
    queue.finish()
    let completed = drained.wait(timeout: .now() + 4) == .success
    if !completed { queue.fail("Output queue did not finish draining before deadline") }
    let final = queue.stats()
    diagnostic(final.merging(["event": "stopped", "backend": "AVCaptureSession",
                              "writer_completed": completed]) { _, new in new })
    return final["error"] == nil && completed ? 0 : 3
}

do {
    let args = Array(CommandLine.arguments.dropFirst())
    if args == ["--list-devices"] {
        let data = try JSONSerialization.data(withJSONObject: devices(), options: [.sortedKeys])
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data([10]))
    } else if args == ["--self-test-queue"] {
        try selfTestQueue()
    } else if args.first == "--self-test" || args.first == "--self-test-pipeline" {
        let rate = args.count > 1 ? Double(args[1]) ?? 0 : 48000
        let seconds = args.count > 2 ? Double(args[2]) ?? 0 : 2
        try selfTest(rate: rate, seconds: seconds, pipeline: args.first == "--self-test-pipeline")
    } else if args.count == 3 && args[0] == "--capture" && args[1] == "--device" {
        exit(try capture(device: args[2]))
    } else {
        diagnostic(["event": "error", "error": "Usage: --list-devices | --capture --device default|coreaudio:ID | --self-test[-pipeline] [rate] [seconds] | --self-test-queue"])
        exit(64)
    }
} catch {
    diagnostic(["event": "error", "error": String(describing: error)])
    exit(3)
}
