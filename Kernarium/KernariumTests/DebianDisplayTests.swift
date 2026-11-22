// SPDX-License-Identifier: GPL-2.0-or-later

import CoreImage
import CoreVideo
import Foundation
import Testing
import UIKit
@testable import Kernarium

/// The real runtime and Phosh compositor, with measured screen configurations
/// supplied to the same owner used by the external UIKit scene.
@MainActor
struct DebianDisplayTests {
    @MainActor private final class Output { var text = "" }

    @Test(.enabled(if: ProcessInfo.processInfo.environment["KERNARIUM_DISPLAY_SMOKE"] == "1"),
          .timeLimit(.minutes(15)))
    func desktopResizesAndReturnsToThePhoneWithoutRestarting() async throws {
        let output = Output()
        try #require(try await eventually(within: .seconds(120)) { EmulatorLauncher.shared.phase != .idle })
        kernarium_runtime_loader_set_console_output_handler { @Sendable data in
            MainActor.assumeIsolated { output.text += String(decoding: data, as: UTF8.self) }
        }
        let window = UIWindow(windowScene: try windowScene())
        let surface = GuestExternalVideoView(frame: CGRect(x: 0, y: 0, width: 640, height: 360))
        window.addSubview(surface)
        window.isHidden = false
        let observer = UUID()
        var frame: CVPixelBuffer?
        defer {
            GuestExternalDisplay.shared.disconnect(surface)
            GuestFrameStream.shared.unsubscribe(id: observer)
            window.isHidden = true
            kernarium_runtime_loader_set_console_output_handler(nil)
            print("DISPLAY_BOOT_LOG:\n\(output.text)")
            Attachment.record(Data(output.text.utf8), named: "display-boot.log")
        }
        func send(_ text: String) { SerialConsoleController.sendToGuest(Data((text + "\n").utf8)) }
        let readyDeadline = ContinuousClock.now.advanced(by: .seconds(600))
        while !output.text.contains("DISPLAY_guest_READY"), ContinuousClock.now < readyDeadline {
            send("test \"$(id -un)\" = debian && pgrep -x phosh >/dev/null && pgrep -x phoc >/dev/null && " +
                 "cat /proc/sys/kernel/random/boot_id > /home/debian/.display-boot-id && " +
                 "pgrep -x phosh > /home/debian/.display-phosh-pid && " +
                 "printf 'display-persistent\\n' > /home/debian/.display-data && printf 'DISPLAY_%s_READY\\n' guest")
            try await Task.sleep(for: .seconds(5))
        }
        try #require(output.text.contains("DISPLAY_guest_READY"))
        try #require(GuestFrameStream.shared.subscribe(id: observer, width: 4095, fps: 10) { frame = $0 } == 0)
        let context = CIContext()
        func verify(_ request: GuestDisplayRequest, name: String) async throws {
            let confirmed = try await eventually(within: .seconds(150), every: .milliseconds(250)) {
                GuestDisplayConfiguration.shared.applied == request
            }
            if !confirmed {
                send("XDG_RUNTIME_DIR=/run/user/1000 " +
                     "WAYLAND_DISPLAY=$(find /run/user/1000 -maxdepth 1 -type s -name 'wayland-*' -printf '%f\\n' | head -n 1) " +
                     "wlr-randr; sudo journalctl -b -u greetd.service -u kernarium-session.service -n 100 --no-pager; " +
                     "printf 'DISPLAY_%s_DONE\\n' diagnostics")
                _ = try await eventually(within: .seconds(15)) { output.text.contains("DISPLAY_diagnostics_DONE") }
            }
            try #require(confirmed, "Debian did not confirm \(request): \(GuestDisplayConfiguration.shared.error ?? "pending")")
            try #require(try await eventually(within: .seconds(60)) {
                guard let frame else { return false }
                return CVPixelBufferGetWidth(frame) == request.width && CVPixelBufferGetHeight(frame) == request.height
            }, "The runtime did not render the confirmed resolution")
            if let frame {
                let image = CIImage(cvPixelBuffer: frame)
                if let cg = context.createCGImage(image, from: image.extent), let png = UIImage(cgImage: cg).pngData() {
                    Attachment.record(png, named: name + ".png")
                }
            }
            let reply = try await GuestSession.request(request.sessionRequest)
            try #require(reply.width == request.width && reply.height == request.height)
            print("DISPLAY_PROOF: \(name) compositor readback and runtime pixels \(request.width)x\(request.height)")
        }
        for (width, height) in [(1280, 720), (1920, 1080)] {
            let request = GuestDisplayRequest(width: width, height: height,
                metadata: GuestScreenMetadata(source: "external", scale: 3, nativeScale: 3,
                    maximumFramesPerSecond: 60))
            GuestExternalDisplay.shared.connect(request, surface: surface)
            try await verify(request, name: "external-\(width)x\(height)")
            try #require(try await eventually(within: .seconds(30)) {
                surface.framesShown > 0 && surface.frameSize == request.pixelSize
            }, "The external video renderer received no matching frames")
        }
        GuestExternalDisplay.shared.disconnect(surface)
        try #require(try await eventually(within: .seconds(150)) {
            GuestDisplayConfiguration.shared.applied?.metadata?.source == "built-in"
        }, "Disconnect did not restore the phone geometry")
        let phone = try #require(GuestDisplayConfiguration.shared.applied)
        try await verify(phone, name: "phone-restored")
        send("test \"$(cat /proc/sys/kernel/random/boot_id)\" = \"$(cat /home/debian/.display-boot-id)\" && " +
             "test \"$(pgrep -x phosh)\" = \"$(cat /home/debian/.display-phosh-pid)\" && " +
             "test \"$(cat /home/debian/.display-data)\" = display-persistent && " +
             "printf 'DISPLAY_%s_OK\\n' unchanged && sudo poweroff")
        try #require(try await eventually(within: .seconds(120)) {
            if case .ended(result: 0) = EmulatorLauncher.shared.phase {
                return output.text.contains("DISPLAY_unchanged_OK") && output.text.contains("reboot: Power down")
            }
            return false
        }, "The display transition restarted the desktop, lost data or failed shutdown")
    }
}
