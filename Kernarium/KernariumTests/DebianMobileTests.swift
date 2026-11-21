// SPDX-License-Identifier: GPL-2.0-or-later

import Foundation
import GhosttyTerminal
import Testing
import UIKit
import Vision
@testable import Kernarium

/// Integration assertions inspect rendered terminal pixels as well as the
/// guest's TTY. These are separate from native finger-gesture UI checks.
@MainActor
struct DebianMobileTests {
    private nonisolated static var requested: Bool {
        ProcessInfo.processInfo.environment["KERNARIUM_MOBILE_SMOKE"] == "1"
    }

    @MainActor
    private final class Output {
        var text = ""
        let console: SerialConsoleController
        init(console: SerialConsoleController) { self.console = console }
        func append(_ data: Data) {
            text += String(decoding: data, as: UTF8.self)
            console.receive(data)
        }
    }

    private func send(_ text: String) { SerialConsoleController.sendToGuest(Data(text.utf8)) }

    private func command(_ text: String, marker: String, output: Output) async throws {
        send(text + "\n")
        try #require(try await eventually(within: .seconds(30)) { output.text.contains(marker) },
                     "Guest command did not finish: \(output.text)")
    }

    private func visible(_ markers: [String], in view: TerminalView, named name: String) async throws {
        var lastImage: UIImage?
        var lastText = ""
        let found = try await eventually(within: .seconds(30)) {
            guard let image = view.snapshotImage(), let cgImage = image.cgImage else { return false }
            lastImage = image
            let request = VNRecognizeTextRequest()
            request.recognitionLevel = .accurate
            request.usesLanguageCorrection = false
            do { try VNImageRequestHandler(cgImage: cgImage).perform([request]) }
            catch { lastText = error.localizedDescription; return false }
            lastText = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
            return markers.allSatisfy { lastText.contains($0) }
        }
        if let png = lastImage?.pngData() { Attachment.record(png, named: name + ".png") }
        try #require(found, "The visible terminal did not show \(markers): OCR=\(lastText)")
    }

    @Test(.enabled(if: DebianMobileTests.requested), .timeLimit(.minutes(15)))
    func realTTYAndVisibleTmuxSurviveViewportChanges() async throws {
        let console = SerialConsoleController(input: { SerialConsoleController.sendToGuest($0) }, resize: { rows, columns in
            Task { @MainActor in GuestTerminalGeometry.shared.resize(rows: rows, columns: columns) }
        })
        let output = Output(console: console)
        try #require(try await eventually(within: .seconds(120)) { EmulatorLauncher.shared.phase != .idle })
        kernarium_runtime_loader_set_console_output_handler { @Sendable data in
            MainActor.assumeIsolated { output.append(data) }
        }
        let window = UIWindow(windowScene: try windowScene())
        window.windowLevel = .alert
        let terminal = console.makeTerminalView()
        window.addSubview(terminal)
        window.frame = CGRect(x: 0, y: 0, width: 390, height: 700)
        terminal.frame = window.bounds
        window.isHidden = false
        window.layoutIfNeeded()
        defer {
            kernarium_runtime_loader_set_console_output_handler(nil)
            console.detach(terminal)
            window.isHidden = true
            print("MOBILE_BOOT_LOG:\n\(output.text)")
            Attachment.record(Data(output.text.utf8), named: "mobile-boot.log")
        }
        let deadline = ContinuousClock.now.advanced(by: .seconds(600))
        while !output.text.contains("MOBILE_guest_READY"), ContinuousClock.now < deadline {
            send("test \"$(id -un)\" = debian && test \"$(tty)\" = /dev/ttyS0 && printf 'MOBILE_%s_READY\\n' guest\n")
            try await Task.sleep(for: .seconds(5))
        }
        try #require(output.text.contains("MOBILE_guest_READY"))
        try #require(try await eventually(within: .seconds(120)) { GuestTerminalGeometry.shared.error == nil && GuestTerminalGeometry.shared.rows > 0 })
        let firstRows = GuestTerminalGeometry.shared.rows
        let firstColumns = GuestTerminalGeometry.shared.columns
        try await command("test \"$(stty size)\" = '\(firstRows) \(firstColumns)' && printf 'GRID_%s_OK\\n' portrait",
                          marker: "GRID_portrait_OK", output: output)
        // The pane markers and status are verified from Metal-rendered
        // screenshots. No tmux capture-pane output participates in this check.
        send("TERM=xterm-256color tmux -L mobile new-session -s mobile 'printf \"TOP_VISIBLE\\n\"; exec bash'\n")
        try await Task.sleep(for: .seconds(2))
        send("\u{02}:")
        send("set-option status-left-length 30; set-option status-left STATUS_VISIBLE\n")
        send("\u{02}\"")
        try await Task.sleep(for: .seconds(1))
        send("printf 'BOTTOM_VISIBLE\\n'\n")
        try await visible(["TOP_VISIBLE", "BOTTOM_VISIBLE", "STATUS_VISIBLE"], in: terminal, named: "tmux-portrait")
        terminal.frame = CGRect(x: 0, y: 0, width: 700, height: 260)
        window.frame = terminal.frame
        window.layoutIfNeeded()
        try #require(try await eventually(within: .seconds(30)) {
            GuestTerminalGeometry.shared.columns > firstColumns && GuestTerminalGeometry.shared.rows < firstRows
        })
        let rows = GuestTerminalGeometry.shared.rows
        let columns = GuestTerminalGeometry.shared.columns
        try await visible(["TOP_VISIBLE", "BOTTOM_VISIBLE", "STATUS_VISIBLE"], in: terminal, named: "tmux-landscape-keyboard")
        send("\u{02}d")
        try await command("test \"$(stty size)\" = '\(rows) \(columns)' && printf 'GRID_%s_OK\\n' changed",
                          marker: "GRID_changed_OK", output: output)
        try await command("tmux -L mobile kill-server; printf 'MOBILE_%s_DONE\\n' checks; sudo poweroff",
                          marker: "MOBILE_checks_DONE", output: output)
        try #require(try await eventually(within: .seconds(120)) {
            if case .ended(result: 0) = EmulatorLauncher.shared.phase { return output.text.contains("reboot: Power down") }
            return false
        })
    }
}
