// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Testing
@testable import Kernarium

/// CI launches the actual iOS runtime twice on an Apple silicon Mac, with
/// one unchanged writable GPT disk in the app container. No automation
/// runtime or rootfs payload changes the guest's boot policy.
@MainActor
struct PersistentDebianTests {
    private nonisolated static var requested: Bool {
        ["write", "read"].contains(
            ProcessInfo.processInfo.environment["KERNARIUM_DEBIAN_SMOKE"] ?? ""
        )
    }

    @MainActor
    private final class Output {
        var text = ""
        func append(_ data: Data) {
            text += String(decoding: data, as: UTF8.self)
            if text.count > 1_000_000 { text = String(text.suffix(1_000_000)) }
        }
    }

    @Test(
        .enabled(if: PersistentDebianTests.requested),
        .timeLimit(.minutes(20))
    )
    func desktopAndConsoleKeepTheSameDisk() async throws {
        let environment = ProcessInfo.processInfo.environment
        let mode = try #require(environment["KERNARIUM_DEBIAN_SMOKE"])
        let writing = mode == "write"
        try #require(environment["KERNARIUM_ROOTFS"] == "debian-13-phosh.img")
        try #require(environment["KERNARIUM_LINUX_CONSOLE_BOOT"] == (writing ? "0" : "1"))
        print("DEBIAN_SMOKE: starting \(mode), rootfs and boot mode received by test host")
        let output = Output()
        // ContentView's console installs the loader's single output handler
        // as it is created. Observe only after that view has started the guest.
        try #require(
            try await eventually(within: .seconds(120)) {
                EmulatorLauncher.shared.phase != .idle
            },
            "The application did not start its Debian guest"
        )
        kernarium_runtime_loader_set_console_output_handler { @Sendable data in
            MainActor.assumeIsolated { output.append(data) }
        }
        defer {
            kernarium_runtime_loader_set_console_output_handler(nil)
            print("DEBIAN_SMOKE: \(mode) serial output:\n\(output.text)")
        }
        let login = "test \"$(id -un)\" = debian && " +
            "test \"$(tty)\" = /dev/ttyS0 && "
        let check = writing
            ? "systemctl is-active --quiet greetd.service && " +
              "pgrep -x phosh >/dev/null && pgrep -x phoc >/dev/null && " +
              "printf 'persistent-debian\\n' > /home/debian/.kernarium-ci"
            : "test \"$(cat /home/debian/.kernarium-ci)\" = persistent-debian && " +
              "systemctl is-active --quiet multi-user.target && " +
              "! systemctl is-active --quiet greetd.service && " +
              "! pgrep -x 'phosh|phoc|squeekboard|Xwayland|gnome-shell' >/dev/null"
        // Splitting the marker prevents terminal echo of the command from
        // satisfying the assertion. A failed check leaves the shell available
        // for a retry while the desktop is still coming up.
        let command = "if " + login + check + "; then sync && " +
            "printf 'DEBIAN_%s_OK\\n' smoke; sudo systemctl poweroff; fi\n"
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: .seconds(900))
        var nextInput = clock.now
        var presentedFrames: UInt64 = 0
        while clock.now < deadline,
              !output.text.contains("DEBIAN_smoke_OK") {
            if case .ended = EmulatorLauncher.shared.phase { break }
            var statistics = kernarium_runtime_statistics()
            statistics.struct_size = UInt32(MemoryLayout<kernarium_runtime_statistics>.size)
            if kernarium_runtime_loader_read_statistics(&statistics) == 0 {
                presentedFrames = max(presentedFrames, statistics.presented_frames)
            }
            if clock.now >= nextInput, !writing || presentedFrames > 0 {
                let bytes = Array(command.utf8)
                bytes.withUnsafeBytes { buffer in
                    if let baseAddress = buffer.baseAddress {
                        _ = kernarium_runtime_loader_send_console_input(
                            baseAddress, buffer.count
                        )
                    }
                }
                nextInput = clock.now.advanced(by: .seconds(15))
            }
            try await Task.sleep(for: .seconds(1))
        }
        if !output.text.contains("DEBIAN_smoke_OK") {
            let diagnostics = Array((
                "id; tty; ps -ef; " +
                "sudo journalctl -b -u greetd.service -n 100 --no-pager\n"
            ).utf8)
            diagnostics.withUnsafeBytes { buffer in
                if let baseAddress = buffer.baseAddress {
                    _ = kernarium_runtime_loader_send_console_input(baseAddress, buffer.count)
                }
            }
            try await Task.sleep(for: .seconds(3))
        }
        try #require(
            output.text.contains("DEBIAN_smoke_OK"),
            """
            Debian \(mode) check failed; phase: \(EmulatorLauncher.shared.phase), presented frames: \(presentedFrames)
            \(output.text)
            """
        )
        let ended = try await eventually(within: .seconds(120)) {
            if case .ended(result: 0) = EmulatorLauncher.shared.phase { return true }
            return false
        }
        try #require(ended, "Debian did not power off cleanly")
        try #require(output.text.contains("reboot: Power down"))
        print(
            "DEBIAN_SMOKE: \(mode) local login, guest checks and clean shutdown completed; " +
                "presented frames: \(presentedFrames)"
        )
    }
}
