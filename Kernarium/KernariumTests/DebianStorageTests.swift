// SPDX-License-Identifier: GPL-2.0-or-later

import Foundation
import Testing
@testable import Kernarium

@MainActor
struct DebianStorageTests {
    private nonisolated static var requested: Bool {
        ProcessInfo.processInfo.environment["KERNARIUM_STORAGE_SMOKE"] == "1"
    }
    @MainActor private final class Output {
        var text = ""
        func append(_ data: Data) { text += String(decoding: data, as: UTF8.self) }
    }

    @Test(.enabled(if: DebianStorageTests.requested), .timeLimit(.minutes(15)))
    func storageGrowthExpandsExt4AndKeepsTheFileAcrossTwoReboots() async throws {
        let environment = ProcessInfo.processInfo.environment
        let phase = try #require(environment["KERNARIUM_STORAGE_PHASE"])
        let output = Output()
        try #require(try await eventually(within: .seconds(120)) { EmulatorLauncher.shared.phase != .idle })
        kernarium_runtime_loader_set_console_output_handler { @Sendable data in
            MainActor.assumeIsolated { output.append(data) }
        }
        defer {
            kernarium_runtime_loader_set_console_output_handler(nil)
            print("STORAGE_BOOT_LOG:\n\(output.text)")
            Attachment.record(Data(output.text.utf8), named: "storage-boot.log")
        }
        let operation: String
        switch phase {
        case "write":
            operation = "printf 'storage-growth-persistent\\n' > /home/debian/.storage-ci; " +
                "df --output=size -B1 / | tail -n 1 > /home/debian/.storage-before"
        case "grow":
            operation = "test \"$(cat /home/debian/.storage-ci)\" = storage-growth-persistent && " +
                "test \"$(df --output=size -B1 / | tail -n 1)\" -gt \"$(cat /home/debian/.storage-before)\" && " +
                "df --output=size -B1 / | tail -n 1 > /home/debian/.storage-after"
        case "read":
            operation = "test \"$(cat /home/debian/.storage-ci)\" = storage-growth-persistent && " +
                "test \"$(df --output=size -B1 / | tail -n 1)\" -eq \"$(cat /home/debian/.storage-after)\""
        default: throw GuestSessionError.message("Unknown storage validation phase")
        }
        let command = "if test \"$(id -un)\" = debian && test \"$(tty)\" = /dev/ttyS0 && " +
            "! pgrep -x 'phosh|phoc|squeekboard' >/dev/null && { \(operation); }; then " +
            "df -B1 /; sync; printf 'STORAGE_%s_OK\\n' \(phase); sudo poweroff; fi\n"
        let marker = "STORAGE_\(phase)_OK"
        let deadline = ContinuousClock.now.advanced(by: .seconds(600))
        while !output.text.contains(marker), ContinuousClock.now < deadline {
            SerialConsoleController.sendToGuest(Data(command.utf8))
            try await Task.sleep(for: .seconds(5))
        }
        try #require(output.text.contains(marker), "\(output.text)")
        try #require(try await eventually(within: .seconds(120)) {
            if case .ended(result: 0) = EmulatorLauncher.shared.phase { return output.text.contains("reboot: Power down") }
            return false
        })
        if phase != "write" {
            #expect(DiskGrowthSettings.pending(filename: "debian-13-phosh.img") == nil)
        } else {
            let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            let capacity = try PersistentDisk.capacity(documents.appendingPathComponent("debian-13-phosh.img"))
            DiskGrowthSettings.schedule(bytes: (capacity / PersistentDisk.gib + 2) * PersistentDisk.gib,
                                        filename: "debian-13-phosh.img")
        }
        print("STORAGE_PROOF: \(phase) df capacity, persistent file and clean shutdown passed")
    }
}
