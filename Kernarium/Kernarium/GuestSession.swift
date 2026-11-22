// SPDX-License-Identifier: GPL-2.0-or-later

import Combine
import Darwin
import Foundation
import UIKit

nonisolated struct GuestSessionRequest: Codable, Sendable {
    var version = 1
    let operation: String
    var text: String?
    var rows: Int?
    var columns: Int?
    var width: Int?
    var height: Int?
    var metadata: GuestScreenMetadata?
}

nonisolated struct GuestSessionReply: Codable, Sendable {
    let version: Int
    let ok: Bool
    var error: String?
    var text: String?
    var rows: Int?
    var columns: Int?
    var width: Int?
    var height: Int?
    var output: String?
}

nonisolated enum GuestSessionError: LocalizedError {
    case message(String)
    var errorDescription: String? {
        switch self { case let .message(message): message }
    }
}

nonisolated enum GuestSession {
    static let maximumMessageBytes = 2 * 1024 * 1024

    static func request(_ request: GuestSessionRequest) async throws -> GuestSessionReply {
        try await Task.detached(priority: .userInitiated) {
            try exchange(request)
        }.value
    }

    private static func exchange(_ request: GuestSessionRequest) throws -> GuestSessionReply {
        let payload = try JSONEncoder().encode(request)
        guard payload.count <= maximumMessageBytes else {
            throw GuestSessionError.message("Clipboard text exceeds 2 MiB.")
        }
        let descriptor = kernarium_runtime_loader_connect_vsock(31337, 1500)
        guard descriptor >= 0 else {
            throw GuestSessionError.message("The guest session service is unavailable (\(descriptor)).")
        }
        defer { Darwin.close(descriptor) }
        var suppressSignal: Int32 = 1
        _ = setsockopt(descriptor, SOL_SOCKET, SO_NOSIGPIPE, &suppressSignal, socklen_t(MemoryLayout<Int32>.size))
        let flags = fcntl(descriptor, F_GETFL)
        guard flags >= 0, fcntl(descriptor, F_SETFL, flags | O_NONBLOCK) == 0 else {
            throw GuestSessionError.message("Cannot configure the guest session socket.")
        }
        let deadline = ProcessInfo.processInfo.systemUptime + 7
        func ready(_ events: Int16) throws {
            while true {
                let remaining = deadline - ProcessInfo.processInfo.systemUptime
                guard remaining > 0 else {
                    throw GuestSessionError.message("The guest session request timed out.")
                }
                var waiting = pollfd(fd: descriptor, events: events, revents: 0)
                let result = poll(&waiting, 1, Int32(min(remaining * 1000, 7000)))
                if result > 0 { return }
                if result < 0, errno != EINTR {
                    throw GuestSessionError.message("The guest session connection failed.")
                }
            }
        }
        func send(_ data: Data) throws {
            var offset = 0
            while offset < data.count {
                try ready(Int16(POLLOUT))
                let count = data.withUnsafeBytes { buffer in
                    Darwin.send(descriptor, buffer.baseAddress!.advanced(by: offset), data.count - offset, 0)
                }
                if count > 0 { offset += count }
                else if count == 0 || (errno != EAGAIN && errno != EINTR) {
                    throw GuestSessionError.message("The guest closed the session connection.")
                }
            }
        }
        func receive(_ length: Int) throws -> Data {
            var data = Data(count: length)
            var offset = 0
            while offset < length {
                try ready(Int16(POLLIN))
                let count = data.withUnsafeMutableBytes { buffer in
                    Darwin.recv(descriptor, buffer.baseAddress!.advanced(by: offset), length - offset, 0)
                }
                if count > 0 { offset += count }
                else if count == 0 || (errno != EAGAIN && errno != EINTR) {
                    throw GuestSessionError.message("The guest closed the session connection.")
                }
            }
            return data
        }
        var length = UInt32(payload.count).bigEndian
        try send(withUnsafeBytes(of: &length) { Data($0) })
        try send(payload)
        let header = try receive(4)
        let replyLength = header.reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
        guard replyLength > 0, replyLength <= UInt32(maximumMessageBytes) else {
            throw GuestSessionError.message("The guest sent an invalid session response.")
        }
        let reply = try JSONDecoder().decode(GuestSessionReply.self, from: receive(Int(replyLength)))
        guard reply.version == 1, reply.ok else {
            throw GuestSessionError.message(reply.error ?? "The guest session request failed.")
        }
        return reply
    }
}

@MainActor
final class GuestClipboard: ObservableObject {
    @Published private(set) var busy = false
    @Published var message: String?
    private let exchange: @Sendable (GuestSessionRequest) async throws -> GuestSessionReply

    init(exchange: @escaping @Sendable (GuestSessionRequest) async throws -> GuestSessionReply = GuestSession.request) {
        self.exchange = exchange
    }

    func phoneToGuest() async {
        guard !busy else { return }
        guard let text = UIPasteboard.general.string else {
            message = "The iPhone clipboard contains no text."
            return
        }
        busy = true
        defer { busy = false }
        do {
            _ = try await exchange(GuestSessionRequest(operation: "clipboard-write", text: text))
            message = "Text copied to the Debian clipboard."
        } catch { message = error.localizedDescription }
    }

    func guestToPhone() async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do {
            let reply = try await exchange(GuestSessionRequest(operation: "clipboard-read"))
            guard let text = reply.text else {
                throw GuestSessionError.message("The guest returned no clipboard text.")
            }
            UIPasteboard.general.string = text
            message = "Text copied to the iPhone clipboard."
        } catch { message = error.localizedDescription }
    }
}

@MainActor
final class GuestTerminalGeometry: ObservableObject {
    static let shared = GuestTerminalGeometry()
    @Published private(set) var rows = 0
    @Published private(set) var columns = 0
    @Published private(set) var error: String?
    private var worker: Task<Void, Never>?

    func resize(rows: Int, columns: Int) {
        guard rows > 0, columns > 0 else { return }
        self.rows = rows
        self.columns = columns
        guard worker == nil else { return }
        worker = Task {
            defer { worker = nil }
            let deadline = ContinuousClock.now.advanced(by: .seconds(120))
            while !Task.isCancelled, ContinuousClock.now < deadline {
                let sentRows = self.rows
                let sentColumns = self.columns
                do {
                    let reply = try await GuestSession.request(GuestSessionRequest(
                        operation: "terminal-size", rows: sentRows, columns: sentColumns
                    ))
                    guard reply.rows == sentRows, reply.columns == sentColumns else {
                        throw GuestSessionError.message("The guest did not apply the terminal size.")
                    }
                    error = nil
                    if self.rows == sentRows, self.columns == sentColumns { return }
                } catch { self.error = error.localizedDescription }
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }
}
