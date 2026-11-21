// SPDX-License-Identifier: GPL-2.0-or-later

import Foundation
import GhosttyTerminal
import Testing
import UIKit
@testable import Kernarium

@MainActor
@Suite(.serialized)
struct DebianAppTests {
    private actor ClipboardPeer {
        var text = ""
        var operations: [String] = []
        func exchange(_ request: GuestSessionRequest) -> GuestSessionReply {
            operations.append(request.operation)
            if request.operation == "clipboard-write" { text = request.text ?? "" }
            return GuestSessionReply(version: 1, ok: true, text: text)
        }
    }

    @Test
    func explicitClipboardActionsPreserveTextWithoutSendingTerminalInput() async {
        let original = UIPasteboard.general.string
        defer { UIPasteboard.general.string = original }
        let text = "Кириллица и «кавычки»\n'одинарные' и \"двойные\"\r\nПоследняя строка\n"
        let peer = ClipboardPeer()
        let clipboard = GuestClipboard(exchange: { await peer.exchange($0) })
        UIPasteboard.general.string = text
        await clipboard.phoneToGuest()
        UIPasteboard.general.string = "different text"
        await clipboard.guestToPhone()
        #expect(UIPasteboard.general.string == text)
        let operations = await peer.operations
        #expect(operations == ["clipboard-write", "clipboard-read"])
        #expect(!clipboard.busy)
    }

    @Test
    func clipboardFailureIsVisibleAndKeepsThePhoneClipboard() async {
        let original = UIPasteboard.general.string
        defer { UIPasteboard.general.string = original }
        UIPasteboard.general.string = "keep this"
        let clipboard = GuestClipboard(exchange: { _ in throw GuestSessionError.message("Not connected") })
        await clipboard.guestToPhone()
        #expect(clipboard.message == "Not connected")
        #expect(UIPasteboard.general.string == "keep this")
        #expect(!clipboard.busy)
    }

    @Test
    func nativeSelectionCopiesPartOfALineAndMultipleLinesIntoAnIOSField() async throws {
        let scene = try windowScene()
        let window = UIWindow(windowScene: scene)
        let root = UIViewController()
        window.rootViewController = root
        window.makeKeyAndVisible()
        defer { window.isHidden = true }
        let contents = "prefix КИРИЛЛИЦА suffix\nsecond 'quoted' line\nthird \"quoted\" line\n"
        for selected in ["КИРИЛЛИЦА", "second 'quoted' line\nthird \"quoted\" line"] {
            let range = (contents as NSString).range(of: selected)
            let selection = ConsoleTextSelectionController(text: contents, range: range)
            root.addChild(selection)
            root.view.addSubview(selection.view)
            selection.view.frame = root.view.bounds
            selection.didMove(toParent: root)
            selection.textView.selectedRange = range
            selection.copySelection()
            let field = UITextView(frame: CGRect(x: 0, y: 0, width: 300, height: 150))
            root.view.addSubview(field)
            _ = field.becomeFirstResponder()
            field.paste(nil)
            try #require(try await eventually { field.text == selected })
            #expect(field.text == selected)
            field.removeFromSuperview()
            selection.willMove(toParent: nil)
            selection.view.removeFromSuperview()
            selection.removeFromParent()
        }
    }

    @Test
    func terminalGridChangesAreForwardedInRowsAndColumns() async throws {
        final class Sizes: @unchecked Sendable {
            let lock = NSLock()
            var values: [(Int, Int)] = []
            func append(_ rows: Int, _ columns: Int) { lock.withLock { values.append((rows, columns)) } }
            var last: (Int, Int)? { lock.withLock { values.last } }
        }
        let sizes = Sizes()
        let console = SerialConsoleController(input: { _ in }, resize: { sizes.append($0, $1) })
        let scene = try windowScene()
        let window = UIWindow(windowScene: scene)
        let view = console.makeTerminalView()
        window.addSubview(view)
        window.isHidden = false
        defer { console.detach(view); window.isHidden = true }
        view.frame = CGRect(x: 0, y: 0, width: 360, height: 600)
        window.layoutIfNeeded()
        try #require(try await eventually { sizes.last != nil })
        let first = try #require(sizes.last)
        view.frame = CGRect(x: 0, y: 0, width: 600, height: 260)
        window.layoutIfNeeded()
        try #require(try await eventually {
            guard let last = sizes.last else { return false }
            return last.0 < first.0 && last.1 > first.1
        })
    }
}
