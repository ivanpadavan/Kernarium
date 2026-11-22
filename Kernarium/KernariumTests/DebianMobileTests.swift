// SPDX-License-Identifier: GPL-2.0-or-later

import Foundation
import CoreImage
import CoreVideo
import GhosttyTerminal
import Testing
import UIKit
import Vision
@testable import Kernarium

private let mobileFrameSink: kernarium_runtime_frame_sink = { context, buffer in
    guard let context, let buffer else { return }
    let relay = Unmanaged<FrameRelay>.fromOpaque(context).takeUnretainedValue()
    relay.receive(Unmanaged<CVPixelBuffer>.fromOpaque(buffer).takeUnretainedValue())
}

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
        // Screen markers use letters so thin punctuation cannot become OCR whitespace.
        var lastImage: UIImage?
        var lastText = ""
        let found = try await eventually(within: .seconds(30), every: .milliseconds(250)) {
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

    private func tmuxCommand(_ text: String, in console: SerialConsoleController) async throws {
        func promptVisible() -> Bool {
            (console.session.readViewportText() ?? "").components(separatedBy: "\n").contains {
                $0.trimmingCharacters(in: .whitespaces).hasPrefix(":")
            }
        }
        send("\u{02}:")
        // Synchronize input with tmux's prompt. Pixel assertions below are
        // the proof of visible panes; this buffer check is readiness only.
        try #require(try await eventually(within: .seconds(30)) {
            promptVisible()
        }, "Ctrl+B did not open the tmux command prompt")
        send(text)
        try #require(try await eventually(within: .seconds(30)) {
            (console.session.readViewportText() ?? "").contains(String(text.suffix(20)))
        }, "tmux did not render the end of its command")
        send("\r")
        let accepted = try await eventually(within: .seconds(30)) { !promptVisible() }
        if !accepted {
            Attachment.record(Data((console.session.readViewportText() ?? "").utf8), named: "tmux-prompt.txt")
            send("\u{1b}")
            _ = try await eventually(within: .seconds(3)) { !promptVisible() }
            send("\u{02}d")
            let detached = try await eventually(within: .seconds(3)) {
                (console.session.readViewportText() ?? "").contains("[detached")
            }
            if detached {
                send("stty -a; tail -n 120 tmux-*.log; printf 'TMUX_%s_DONE\\n' DIAGNOSTICS\n")
                _ = try await eventually(within: .seconds(3)) {
                    (console.session.readViewportText() ?? "").contains("TMUX_DIAGNOSTICS_DONE")
                }
            }
        }
        try #require(accepted, "tmux did not finish its command prompt after a separate Return key")
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
            send("test \"$(id -un)\" = debian && test \"$(tty)\" = /dev/ttyS0 && pgrep -x phosh >/dev/null && pgrep -x phoc >/dev/null && printf 'MOBILE_%s_READY\\n' guest\n")
            try await Task.sleep(for: .seconds(5))
        }
        try #require(output.text.contains("MOBILE_guest_READY"))
        try await clipboard(output)
        try await graphicalTerminal(output)
        try #require(try await eventually(within: .seconds(120)) { GuestTerminalGeometry.shared.error == nil && GuestTerminalGeometry.shared.rows > 0 })
        let firstRows = GuestTerminalGeometry.shared.rows
        let firstColumns = GuestTerminalGeometry.shared.columns
        try await command("test \"$(stty size)\" = '\(firstRows) \(firstColumns)' && printf 'GRID_%s_OK\\n' portrait",
                          marker: "GRID_portrait_OK", output: output)
        // The pane markers and status are verified from Metal-rendered
        // screenshots. No tmux capture-pane output participates in this check.
        send("tmux -V; stty -a; TERM=xterm-256color tmux -vv -L mobile new-session -s mobile 'printf \"TOP%s\\n\" VISIBLE; exec bash'\n")
        try await visible(["TOPVISIBLE"], in: terminal, named: "tmux-attached")
        try await tmuxCommand("set-option status-left-length 30; set-option status-left STATUSVISIBLE", in: console)
        try await visible(["STATUSVISIBLE"], in: terminal, named: "tmux-status")
        try await tmuxCommand("split-window -v 'printf \"BOTTOM%s\\n\" VISIBLE; exec bash'", in: console)
        try await visible(["TOPVISIBLE", "BOTTOMVISIBLE", "STATUSVISIBLE"], in: terminal, named: "tmux-portrait")
        terminal.frame = CGRect(x: 0, y: 0, width: 700, height: 260)
        window.frame = terminal.frame
        window.layoutIfNeeded()
        try #require(try await eventually(within: .seconds(30)) {
            GuestTerminalGeometry.shared.columns > firstColumns && GuestTerminalGeometry.shared.rows < firstRows
        })
        let rows = GuestTerminalGeometry.shared.rows
        let columns = GuestTerminalGeometry.shared.columns
        try await visible(["TOPVISIBLE", "BOTTOMVISIBLE", "STATUSVISIBLE"], in: terminal, named: "tmux-landscape-keyboard")
        send("\u{02}d")
        try #require(try await eventually(within: .seconds(30)) { output.text.contains("[detached") })
        try await command("test \"$(stty size)\" = '\(rows) \(columns)' && printf 'GRID_%s_OK\\n' changed",
                          marker: "GRID_changed_OK", output: output)
        try await command("tmux -L mobile kill-server; printf 'MOBILE_%s_DONE\\n' checks; sudo poweroff",
                          marker: "MOBILE_checks_DONE", output: output)
        try #require(try await eventually(within: .seconds(120)) {
            if case .ended(result: 0) = EmulatorLauncher.shared.phase { return output.text.contains("reboot: Power down") }
            return false
        })
    }

    private func graphicalTerminal(_ output: Output) async throws {
        let relay = FrameRelay()
        let context = CIContext()
        var frame: CGImage?
        relay.handler = { buffer in
            let image = CIImage(cvPixelBuffer: buffer)
            frame = context.createCGImage(image, from: image.extent)
        }
        try #require(kernarium_runtime_loader_set_frame_sink(mobileFrameSink,
            Unmanaged.passUnretained(relay).toOpaque(), 1280, 5) == 0)
        defer { _ = kernarium_runtime_loader_set_frame_sink(nil, nil, 0, 0) }
        func screen(_ marker: String, named name: String) async throws {
            var text = ""
            let found = try await eventually(within: .seconds(60), every: .milliseconds(500)) {
                guard let frame else { return false }
                let request = VNRecognizeTextRequest()
                request.recognitionLevel = .accurate
                request.usesLanguageCorrection = false
                do { try VNImageRequestHandler(cgImage: frame).perform([request]) }
                catch { text = error.localizedDescription; return false }
                text = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
                return text.contains(marker)
            }
            if let frame, let png = UIImage(cgImage: frame).pngData() { Attachment.record(png, named: name + ".png") }
            if !found {
                send("cat /tmp/kernarium-kgx.log; ps -eo pid,comm,args; journalctl --user -b -n 80 --no-pager; " +
                     "printf 'GRAPHIC_%s_DONE\\n' diagnostics\n")
                _ = try await eventually(within: .seconds(10)) { output.text.contains("GRAPHIC_diagnostics_DONE") }
            }
            try #require(found, "Graphical terminal did not display \(marker): \(text)")
        }
        func key(_ code: UInt16, modifiers: [UInt16] = []) {
            for modifier in modifiers { kernarium_runtime_loader_send_key(modifier, 1) }
            kernarium_runtime_loader_send_key(code, 1)
            kernarium_runtime_loader_send_key(code, 0)
            for modifier in modifiers.reversed() { kernarium_runtime_loader_send_key(modifier, 0) }
        }
        func paste(_ text: String, ready: String) async throws {
            let reply = try await GuestSession.request(GuestSessionRequest(operation: "clipboard-write", text: text))
            try #require(reply.ok)
            key(47, modifiers: [29, 42]) // Ctrl+Shift+V
            // Submit only after the graphical terminal has rendered the
            // paste; clipboard transfer and key delivery are separate events.
            try await screen(ready, named: "graphical-paste-ready")
        }
        try #require(try await eventually(within: .seconds(60)) { frame != nil })
        // A first compositor frame can precede the completed GNOME session.
        // Wait for its target and screen-lock service before sending a gesture.
        try #require(try await eventually(within: .seconds(120), every: .seconds(2)) {
            send("XDG_RUNTIME_DIR=/run/user/1000 systemctl --user is-active --quiet graphical-session.target && " +
                 "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus " +
                 "gdbus call --session --dest org.gnome.ScreenSaver --object-path /org/gnome/ScreenSaver " +
                 "--method org.gnome.ScreenSaver.GetActive && printf 'GRAPHIC_%s_READY\\n' session\n")
            return output.text.contains("GRAPHIC_session_READY")
        }, "The graphical session did not finish starting")
        // The initial Phosh session has a lock screen. Use the guest's normal
        // touchscreen swipe instead of changing its lock policy for the test.
        for step in 0...28 {
            kernarium_runtime_loader_send_touch(0, 16384, UInt32(32000 - step * 1000), 1)
            try await Task.sleep(for: .milliseconds(50))
        }
        kernarium_runtime_loader_send_touch(0, 16384, 4000, 0)
        try await command("loginctl list-sessions --no-legend; " +
                          "session=$(loginctl show-user debian -p Display --value); " +
                          "test -z \"$session\" || loginctl show-session \"$session\" -p Type -p LockedHint; " +
                          "printf 'GRAPHIC_%s_DONE\\n' swipe", marker: "GRAPHIC_swipe_DONE", output: output)
        try await command("(XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus " +
                          "WAYLAND_DISPLAY=$(find /run/user/1000 -maxdepth 1 -type s -name 'wayland-*' -printf '%f\\n' | head -n 1) " +
                          "kgx -- env PS1=GRAPHICREADY /bin/bash --noprofile --norc >/tmp/kernarium-kgx.log 2>&1 &); " +
                          "printf 'GRAPHIC_%s_STARTED\\n' terminal", marker: "GRAPHIC_terminal_STARTED", output: output)
        try await screen("GRAPHICREADY", named: "graphical-console-ready")
        try await paste("printf 'GUIKEYTYPED\\n' > /home/debian/.graphical-keys", ready: "GUIKEYTYPED")
        try await command("test ! -e /home/debian/.graphical-keys && printf 'GRAPHIC_%s_OK\\n' notexecuted",
                          marker: "GRAPHIC_notexecuted_OK", output: output)
        key(28)
        try await command("test \"$(cat /home/debian/.graphical-keys)\" = GUIKEYTYPED && printf 'GRAPHIC_%s_OK\\n' pasted",
                          marker: "GRAPHIC_pasted_OK", output: output)
        try await paste("tmux -L graphical new-session -s graphical 'printf \"GRAPHIC%sREADY\\n\" TMUX; exec bash'", ready: "TMUX")
        key(28)
        try await screen("GRAPHICTMUXREADY", named: "graphical-tmux-ready")
        key(48, modifiers: [29]) // Ctrl+B
        key(46) // c: a new tmux window
        try #require(try await eventually(within: .seconds(30), every: .seconds(1)) {
            send("test \"$(tmux -L graphical list-windows -F '#{window_index}' | wc -l)\" -eq 2 && printf 'GRAPHIC_%s_OK\\n' prefix\n")
            return output.text.contains("GRAPHIC_prefix_OK")
        })
        try await paste("printf 'GRAPHICPREFIX%s\\n' OK", ready: "GRAPHICPREFIX")
        key(28)
        try await screen("GRAPHICPREFIXOK", named: "graphical-tmux-prefix")
        send("tmux -L graphical kill-server; cat /tmp/kernarium-kgx.log; " +
             "gdbus call --session --dest sm.puri.OSK0 --object-path /sm/puri/OSK0 " +
             "--method org.freedesktop.DBus.Properties.Get sm.puri.OSK0 Visible\n")
        print("MOBILE_PROOF: real GNOME Console accepted Ctrl+Shift+V without execution before Enter; Ctrl+B created a second tmux window and its output was visible")
    }

    private func clipboard(_ output: Output) async throws {
        let previous = UIPasteboard.general.string
        defer { UIPasteboard.general.string = previous }
        let toGuest = "touch /home/debian/.clipboard-must-not-run\nКириллица: «кавычки», 'single', \"double\"\r\nПоследняя строка\n"
        let clipboard = GuestClipboard()
        UIPasteboard.general.string = toGuest
        await clipboard.phoneToGuest()
        try #require(clipboard.message == "Text copied to the Debian clipboard.", "\(clipboard.message ?? "No result")")
        let received = try await GuestSession.request(GuestSessionRequest(operation: "clipboard-read"))
        #expect(Array((received.text ?? "").utf8) == Array(toGuest.utf8))
        let fromGuest = "Debian → iPhone\n'Одинарные', \"двойные\"\r\nЕщё строка\n\n"
        let encoded = Data(fromGuest.utf8).base64EncodedString()
        try await command("printf %s '\(encoded)' | base64 -d | XDG_RUNTIME_DIR=/run/user/1000 " +
                          "WAYLAND_DISPLAY=$(find /run/user/1000 -maxdepth 1 -type s -name 'wayland-*' -printf '%f\\n' | head -n 1) wl-copy && " +
                          "test ! -e /home/debian/.clipboard-must-not-run && printf 'NATIVE_%s_READY\\n' clipboard",
                          marker: "NATIVE_clipboard_READY", output: output)
        await clipboard.guestToPhone()
        try #require(clipboard.message == "Text copied to the iPhone clipboard.", "\(clipboard.message ?? "No result")")
        #expect(Array((UIPasteboard.general.string ?? "").utf8) == Array(fromGuest.utf8))
        print("MOBILE_PROOF: native iOS clipboard and real Wayland clipboard preserved exact UTF-8 in both directions without executing copied commands")
    }
}
