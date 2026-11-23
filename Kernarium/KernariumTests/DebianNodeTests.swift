// SPDX-License-Identifier: GPL-2.0-or-later

import Foundation
import Testing
@testable import Kernarium

@MainActor
struct DebianNodeTests {
    private nonisolated static var requested: Bool {
        ProcessInfo.processInfo.environment["KERNARIUM_NODE_SMOKE"] == "1"
    }

    private final class Output {
        var text = ""
    }

    @Test(.enabled(if: DebianNodeTests.requested), .timeLimit(.minutes(15)))
    func preinstalledNodeReportsVersionAndRunsJavaScript() async throws {
        let output = Output()
        try #require(try await eventually(within: .seconds(120)) {
            EmulatorLauncher.shared.phase != .idle
        })
        kernarium_runtime_loader_set_console_output_handler { @Sendable data in
            MainActor.assumeIsolated { output.text += String(decoding: data, as: UTF8.self) }
        }
        defer {
            kernarium_runtime_loader_set_console_output_handler(nil)
            print("NODE_GUEST_LOG:\n\(output.text)")
        }
        let ready = "test \"$(id -un)\" = debian && mountpoint -q /opt/kernarium-node && printf 'NODE_%s_READY\\n' guest\n"
        let deadline = ContinuousClock.now.advanced(by: .seconds(600))
        while ContinuousClock.now < deadline, !output.text.contains("NODE_guest_READY") {
            SerialConsoleController.sendToGuest(Data(ready.utf8))
            try await Task.sleep(for: .seconds(10))
        }
        try #require(output.text.contains("NODE_guest_READY"), "Node capsule did not mount: \(output.text)")
        let command = "test \"$(node --version)\" = v22.16.0 && printf 'NODE_%s_OK\\n' version && node /usr/local/share/kernarium/node-aot-test.js\n"
        SerialConsoleController.sendToGuest(Data(command.utf8))
        try #require(try await eventually(within: .seconds(180)) {
            output.text.contains("NODE_version_OK") && output.text.contains("NODE_AOT_WORKLOAD_OK")
        }, "Preinstalled Node failed: \(output.text)")
        SerialConsoleController.sendToGuest(Data("sudo systemctl poweroff\n".utf8))
        try #require(try await eventually(within: .seconds(120)) {
            if case .ended(result: 0) = EmulatorLauncher.shared.phase { return true }
            return false
        })
    }
}
