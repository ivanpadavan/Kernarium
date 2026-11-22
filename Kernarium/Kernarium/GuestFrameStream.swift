// SPDX-License-Identifier: GPL-2.0-or-later

import CoreVideo
import Foundation

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
    private lazy var mirror = FrameMirror { [weak self] buffer in
        guard let self else { return }
        for consumer in Array(self.consumers.values) { consumer.receive(buffer) }
    }

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
            mirror.disarm()
            return 0
        }
        return mirror.arm(width: consumers.values.map(\.width).max()!,
                          framesPerSecond: consumers.values.map(\.fps).max()!)
    }
}
