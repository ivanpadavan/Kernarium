// SPDX-License-Identifier: GPL-2.0-or-later

import Foundation
import GuestHostCore
import MetalKit
import Testing
import UIKit
@testable import Kernarium

@MainActor
@Suite(.serialized)
struct GuestDisplayTests {
    @Test
    func geometryNeedsNoOptionalMetadata() throws {
        let request = try JSONDecoder().decode(GuestDisplayRequest.self,
            from: Data(#"{"width":1280,"height":720}"#.utf8))
        #expect(try request.validated().pixelSize == CGSize(width: 1280, height: 720))
        let object = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(request.sessionRequest)) as? [String: Any])
        #expect(object["operation"] as? String == "display-scale")
        #expect(object["metadata"] == nil)
    }

    @Test
    func hostScaleIsAdvisoryAndMetadataSurvivesEncoding() throws {
        let metadata = GuestScreenMetadata(source: "external",
            sizePoints: GuestScreenSize(width: 640, height: 360),
            nativePixels: GuestScreenSize(width: 1920, height: 1080), scale: 3, nativeScale: 3,
            availableModes: [GuestScreenMode(width: 1920, height: 1080, pixelAspectRatio: 1)],
            maximumFramesPerSecond: 60,
            audioRoutes: [GuestAudioRoute(name: "Test audio route", type: "AirPlay")])
        let original = GuestDisplayRequest(width: 1280, height: 720, metadata: metadata)
        let decoded = try JSONDecoder().decode(GuestDisplayRequest.self, from: JSONEncoder().encode(original))
        #expect(decoded == original)
        #expect(try decoded.validated().pixelSize == CGSize(width: 1280, height: 720))
    }

    @Test
    func invalidGeometryShowsAnErrorWithoutSendingARequest() async {
        let configuration = GuestDisplayConfiguration(exchange: { _ in
            Issue.record("Invalid geometry reached the guest")
            return GuestSessionReply(version: 1, ok: true)
        })
        configuration.guestRunningChanged(true)
        defer { configuration.guestRunningChanged(false) }
        for size in [(0, 720), (1280, -1), (4096, 720)] {
            configuration.configure(GuestDisplayRequest(width: size.0, height: size.1))
            #expect(configuration.error != nil)
            #expect(configuration.applied == nil)
        }
    }

    private actor Peer {
        var sizes: [Int] = []
        func exchange(_ request: GuestSessionRequest) async throws -> GuestSessionReply {
            sizes.append(request.width ?? 0)
            try await Task.sleep(for: .milliseconds(100))
            return GuestSessionReply(version: 1, ok: true, width: request.width, height: request.height)
        }
    }

    @Test
    func aNewGeometryReplacesAnInFlightRequest() async throws {
        let peer = Peer()
        let configuration = GuestDisplayConfiguration(exchange: { try await peer.exchange($0) })
        configuration.guestRunningChanged(true)
        defer { configuration.guestRunningChanged(false) }
        configuration.configure(GuestDisplayRequest(width: 1280, height: 720))
        try await Task.sleep(for: .milliseconds(20))
        let final = GuestDisplayRequest(width: 1920, height: 1080)
        configuration.configure(final)
        try #require(try await eventually { configuration.applied == final })
        #expect(await peer.sizes == [1280, 1920])
        #expect(configuration.error == nil)
    }

    @Test
    func externalPixelsOverrideAndRestoreBothPhoneScales() throws {
        let window = UIWindow(windowScene: try windowScene())
        let view = GuestMetalView(frame: CGRect(x: 0, y: 0, width: 390, height: 700), device: MTLCreateSystemDefaultDevice())
        window.addSubview(view)
        window.isHidden = false
        defer { window.isHidden = true }
        for scale in [GuestDisplayScale.points, .native] {
            view.scale = scale
            let phone = try #require(GuestDisplayLayout(scale: scale, points: view.bounds.size,
                nativeScale: Double(window.screen.nativeScale)))
            view.pixelSizeOverride = CGSize(width: 1920, height: 1080)
            view.reportLayout()
            #expect(view.drawableSize == CGSize(width: 1920, height: 1080))
            view.pixelSizeOverride = nil
            view.reportLayout()
            #expect(view.drawableSize == phone.pixelSize)
        }
    }
}
