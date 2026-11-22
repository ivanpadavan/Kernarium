// SPDX-License-Identifier: GPL-2.0-or-later

import Combine
import Foundation

nonisolated struct GuestScreenSize: Codable, Equatable, Sendable {
    let width: Double
    let height: Double
}

nonisolated struct GuestScreenMode: Codable, Equatable, Sendable {
    let width: Int
    let height: Int
    var pixelAspectRatio: Double?
}

nonisolated struct GuestScreenInsets: Codable, Equatable, Sendable {
    let top: Double
    let left: Double
    let bottom: Double
    let right: Double
}

nonisolated struct GuestAudioRoute: Codable, Equatable, Sendable {
    let name: String
    let type: String
}

/// Reported host facts, never a command to change the desktop's UI scale.
nonisolated struct GuestScreenMetadata: Codable, Equatable, Sendable {
    var source: String?
    var sizePoints: GuestScreenSize?
    var nativePixels: GuestScreenSize?
    var scale: Double?
    var nativeScale: Double?
    var availableModes: [GuestScreenMode]?
    var currentMode: GuestScreenMode?
    var preferredMode: GuestScreenMode?
    var maximumFramesPerSecond: Int?
    var overscanInsetsPoints: GuestScreenInsets?
    var audioRoutes: [GuestAudioRoute]?
    var colorGamut: String?
    var currentEDRHeadroom: Double?
    var potentialEDRHeadroom: Double?
}

nonisolated struct GuestDisplayRequest: Codable, Equatable, Sendable {
    let width: Int
    let height: Int
    var metadata: GuestScreenMetadata?

    var pixelSize: CGSize { CGSize(width: width, height: height) }

    func validated() throws -> Self {
        guard (1...4095).contains(width), (1...4095).contains(height) else {
            throw GuestSessionError.message("The guest display must be between 1 and 4095 pixels on each axis.")
        }
        return self
    }

    var sessionRequest: GuestSessionRequest {
        GuestSessionRequest(operation: "display-scale", width: width, height: height, metadata: metadata)
    }
}

/// Serializes changes and coalesces requests while the guest's desktop starts.
@MainActor
final class GuestDisplayConfiguration: ObservableObject {
    static let shared = GuestDisplayConfiguration()
    @Published private(set) var applied: GuestDisplayRequest?
    @Published var error: String?
    private var desired: GuestDisplayRequest?
    private var worker: Task<Void, Never>?
    private var running = false
    private let exchange: @Sendable (GuestSessionRequest) async throws -> GuestSessionReply

    init(exchange: @escaping @Sendable (GuestSessionRequest) async throws -> GuestSessionReply = GuestSession.request) {
        self.exchange = exchange
    }

    func guestRunningChanged(_ running: Bool) {
        self.running = running
        if !running { desired = nil; applied = nil; error = nil }
        startIfNeeded()
    }

    func configure(_ request: GuestDisplayRequest) {
        do { desired = try request.validated() }
        catch { self.error = error.localizedDescription; return }
        startIfNeeded()
    }

    func retry() { error = nil; startIfNeeded() }

    private func startIfNeeded() {
        guard running, worker == nil, let desired, desired != applied else { return }
        worker = Task { [weak self] in
            guard let self else { return }
            defer { self.worker = nil }
            var target = desired
            var deadline = ContinuousClock.now.advanced(by: .seconds(120))
            while self.running, let latest = self.desired {
                if latest != target { target = latest; deadline = ContinuousClock.now.advanced(by: .seconds(120)) }
                do {
                    let reply = try await self.exchange(target.sessionRequest)
                    guard reply.ok, reply.width == target.width, reply.height == target.height else {
                        throw GuestSessionError.message(reply.error ?? "Debian did not confirm the requested display resolution.")
                    }
                    guard self.running else { return }
                    if self.desired == target {
                        self.applied = target
                        self.error = nil
                        return
                    }
                } catch {
                    guard self.running else { return }
                    if self.desired == target, ContinuousClock.now >= deadline {
                        self.error = error.localizedDescription
                        return
                    }
                    try? await Task.sleep(for: .seconds(1))
                }
            }
        }
    }
}
