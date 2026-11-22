// SPDX-License-Identifier: GPL-2.0-or-later

import CoreVideo
import Foundation

private let sharedGuestFrameSink: kernarium_runtime_frame_sink = { context, buffer in
    guard let context, let buffer else { return }
    Unmanaged<FrameRelay>.fromOpaque(context).takeUnretainedValue()
        .receive(Unmanaged<CVPixelBuffer>.fromOpaque(buffer).takeUnretainedValue())
}

/// One runtime mirror shared by PiP and the external display.
@MainActor
final class GuestFrameStream {
    static let shared = GuestFrameStream()
    private struct Consumer {
        let width: UInt32
        let fps: UInt32
        let receive: (CVPixelBuffer) -> Void
    }
    private var consumers: [UUID: Consumer] = [:]
    private var relay: FrameRelay?

    func subscribe(id: UUID, width: UInt32, fps: UInt32, receive: @escaping (CVPixelBuffer) -> Void) -> Int32 {
        let previous = consumers[id]
        consumers[id] = Consumer(width: width, fps: fps, receive: receive)
        let result = configure()
        if result != 0 { consumers[id] = previous; _ = configure() }
        return result
    }

    func unsubscribe(id: UUID) {
        guard consumers.removeValue(forKey: id) != nil else { return }
        _ = configure()
    }

    private func configure() -> Int32 {
        guard !consumers.isEmpty else {
            let result = kernarium_runtime_loader_set_frame_sink(nil, nil, 0, 0)
            if result == 0 { relay = nil }
            return result
        }
        if relay == nil {
            let relay = FrameRelay()
            relay.handler = { [weak self] buffer in
                guard let self else { return }
                for consumer in Array(self.consumers.values) { consumer.receive(buffer) }
            }
            self.relay = relay
        }
        return kernarium_runtime_loader_set_frame_sink(sharedGuestFrameSink,
            Unmanaged.passUnretained(relay!).toOpaque(),
            consumers.values.map(\.width).max()!, consumers.values.map(\.fps).max()!)
    }
}
