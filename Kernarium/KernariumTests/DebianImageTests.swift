// SPDX-License-Identifier: GPL-2.0-or-later

import Foundation
import Testing
@testable import Kernarium

/// A prebuilt validation host tests new images without building an IPA or
/// running application UI tests. The image owns all accounts and services.
@MainActor
struct DebianImageTests {
    private nonisolated static var requested: Bool {
        ProcessInfo.processInfo.environment["KERNARIUM_IMAGE_SMOKE"] == "1"
    }

    @MainActor
    private final class Output {
        var text = ""
        func append(_ data: Data) {
            text += String(decoding: data, as: UTF8.self)
            if text.count > 2_000_000 { text = String(text.suffix(2_000_000)) }
        }
    }

    private func send(_ command: String) {
        SerialConsoleController.sendToGuest(Data(command.utf8))
    }

    private func quote(_ text: String) -> String {
        "'" + text.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    private func command(_ command: String, produces marker: String, in output: Output) async throws {
        send(command + "\n")
        try #require(
            try await eventually(within: .seconds(30)) { output.text.contains(marker) },
            "The guest command did not complete: \(output.text)"
        )
    }

    @Test(.enabled(if: DebianImageTests.requested), .timeLimit(.minutes(15)))
    func imageBootLoginServicesAndPersistence() async throws {
        let environment = ProcessInfo.processInfo.environment
        let kind = try #require(environment["KERNARIUM_IMAGE_KIND"])
        try #require(["phosh", "minimal", "erofs", "ext4"].contains(kind))
        let writing = environment["KERNARIUM_IMAGE_PHASE"] == "write"
        let graphical = kind == "phosh" && writing
        let account = kind == "phosh" ? "debian" : "root"
        let privilege = account == "root" ? "" : "sudo "
        let home = kind == "phosh" ? "/home/debian" : "/root"
        let markerFile = home + "/.kernarium-image-ci"
        let output = Output()
        try #require(try await eventually(within: .seconds(120)) {
            EmulatorLauncher.shared.phase != .idle
        })
        kernarium_runtime_loader_set_console_output_handler { @Sendable data in
            MainActor.assumeIsolated { output.append(data) }
        }
        defer {
            kernarium_runtime_loader_set_console_output_handler(nil)
            print("IMAGE_BOOT_LOG:\n\(output.text)")
            Attachment.record(Data(output.text.utf8), named: "image-boot.log")
        }
        let desktop = graphical
            ? "pgrep -x phosh >/dev/null && pgrep -x phoc >/dev/null"
            : "systemctl is-active --quiet multi-user.target && ! pgrep -x 'phosh|phoc|squeekboard|Xwayland|gnome-shell' >/dev/null"
        let ready = "if test \"$(id -un)\" = \(account) && test \"$(tty)\" = /dev/ttyS0 && \(desktop); " +
            "then printf 'IMAGE_%s_READY\\n' guest; fi\n"
        let deadline = ContinuousClock.now.advanced(by: .seconds(600))
        var nextInput = ContinuousClock.now
        while ContinuousClock.now < deadline, !output.text.contains("IMAGE_guest_READY") {
            if case .ended = EmulatorLauncher.shared.phase { break }
            if ContinuousClock.now >= nextInput {
                send(ready)
                nextInput = ContinuousClock.now.advanced(by: .seconds(10))
            }
            try await Task.sleep(for: .seconds(1))
        }
        try #require(output.text.contains("IMAGE_guest_READY"), "Image boot/login failed: \(output.text)")
        if graphical {
            var statistics = kernarium_runtime_statistics()
            statistics.struct_size = UInt32(MemoryLayout<kernarium_runtime_statistics>.size)
            try #require(kernarium_runtime_loader_read_statistics(&statistics) == 0)
            try #require(statistics.presented_frames > 0)
        }
        if environment["KERNARIUM_IMAGE_FEATURES"] == "1" {
            let reply = try await GuestSession.request(GuestSessionRequest(operation: "terminal-size", rows: 30, columns: 100))
            #expect(reply.rows == 30)
            #expect(reply.columns == 100)
            try await command("test \"$(stty size)\" = '30 100' && printf 'TTY_%s_OK\\n' image",
                              produces: "TTY_image_OK", in: output)
            if graphical {
                try await clipboardChecks(output)
                try await command("pgrep -x squeekboard >/dev/null && printf 'OSK_%s_OK\\n' image",
                                  produces: "OSK_image_OK", in: output)
            }
        }
        let persistence: String
        if writing {
            persistence = "printf 'image-persistence\\n' > \(markerFile)"
        } else if ["erofs", "ext4"].contains(kind) {
            persistence = "test ! -e \(markerFile)"
        } else {
            persistence = "test \"$(cat \(markerFile))\" = image-persistence"
        }
        let shutdown = "if \(persistence) && sync; then printf 'IMAGE_%s_CHECKED\\n' disk; " +
            "\(privilege)journalctl -b _UID=1000 -n 150 --no-pager; \(privilege)systemctl poweroff; fi"
        try await command(shutdown, produces: "IMAGE_disk_CHECKED", in: output)
        try #require(try await eventually(within: .seconds(120)) {
            if case .ended(result: 0) = EmulatorLauncher.shared.phase {
                return output.text.contains("reboot: Power down")
            }
            return false
        }, "The image did not power off cleanly: \(output.text)")
        print("IMAGE_PROOF: \(kind) \(writing ? "write" : "read") boot, login, services, storage and poweroff passed")
    }

    private func clipboardChecks(_ output: Output) async throws {
        let first = "touch /home/debian/.clipboard-must-not-execute\nКириллица: «кавычки», 'single', \"double\"\r\nПоследняя строка\n"
        _ = try await GuestSession.request(GuestSessionRequest(operation: "clipboard-write", text: first))
        let received = try await GuestSession.request(GuestSessionRequest(operation: "clipboard-read"))
        #expect(received.text == first)
        let second = "Debian → iPhone\nВторая строка: 'текст' и \"текст\"\n\n"
        try await command(
            "printf %s \(quote(second)) | XDG_RUNTIME_DIR=/run/user/1000 " +
                "WAYLAND_DISPLAY=$(find /run/user/1000 -maxdepth 1 -type s -name 'wayland-*' -printf '%f\\n' | head -n 1) wl-copy && " +
                "test ! -e /home/debian/.clipboard-must-not-execute && printf 'CLIP_%s_SET\\n' guest",
            produces: "CLIP_guest_SET", in: output
        )
        let fromGuest = try await GuestSession.request(GuestSessionRequest(operation: "clipboard-read"))
        #expect(fromGuest.text == second)
        print("IMAGE_PROOF: both clipboard directions preserved multiline UTF-8 text; clipboard commands were not executed")
    }
}
