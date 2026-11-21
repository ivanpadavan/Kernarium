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

    @Test(.enabled(if: PersistentDebianTests.requested))
    func desktopAndConsoleKeepTheSameDisk() async throws {
        let writing = ProcessInfo.processInfo.environment[
            "KERNARIUM_DEBIAN_SMOKE"
        ] == "write"
        let output = Output()
        kernarium_runtime_loader_set_console_output_handler { @Sendable data in
            MainActor.assumeIsolated { output.append(data) }
        }
        defer { kernarium_runtime_loader_set_console_output_handler(nil) }
        let check = writing
            ? "pgrep -x phosh >/dev/null && pgrep -x phoc >/dev/null && " +
              "printf 'persistent-debian\\n' > /home/debian/.kernarium-ci"
            : "test \"$(cat /home/debian/.kernarium-ci)\" = persistent-debian && " +
              "! pgrep -x phosh >/dev/null && ! pgrep -x phoc >/dev/null"
        // Splitting the marker prevents terminal echo of the command from
        // satisfying the assertion. A failed check leaves the shell available
        // for a retry while the desktop is still coming up.
        let command = "if " + check + "; then sync; " +
            "printf 'DEBIAN_%s_OK\\n' smoke; sudo systemctl poweroff; fi\n"
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: .seconds(900))
        var nextInput = clock.now
        while clock.now < deadline,
              !output.text.contains("DEBIAN_smoke_OK") {
            if clock.now >= nextInput {
                let bytes = Array(command.utf8)
                _ = bytes.withUnsafeBytes {
                    kernarium_runtime_loader_send_console_input(
                        $0.baseAddress, $0.count
                    )
                }
                nextInput = clock.now.advanced(by: .seconds(15))
            }
            try await Task.sleep(for: .seconds(1))
        }
        #expect(
            output.text.contains("DEBIAN_smoke_OK"),
            "Debian boot/persistence check failed: \(output.text)"
        )
        let ended = try await eventually(within: .seconds(120)) {
            if case .ended(result: 0) = EmulatorLauncher.shared.phase { return true }
            return false
        }
        #expect(ended, "Debian did not power off cleanly")
    }
}
