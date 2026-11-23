// SPDX-License-Identifier: GPL-2.0-or-later

import AVFoundation
import Combine
import CoreMedia
import CoreVideo
import GuestHostCore
import SwiftUI
import UIKit

@MainActor
enum GuestScreenFacts {
    static func metadata(screen: UIScreen, bounds: CGRect, traits: UITraitCollection, source: String) -> GuestScreenMetadata {
        let native = screen.nativeBounds.size
        let insets = screen.overscanCompensationInsets
        func mode(_ mode: UIScreenMode) -> GuestScreenMode {
            GuestScreenMode(width: Int(mode.size.width), height: Int(mode.size.height),
                            pixelAspectRatio: Double(mode.pixelAspectRatio))
        }
        let routes = AVAudioSession.sharedInstance().currentRoute.outputs.map {
            GuestAudioRoute(name: $0.portName, type: $0.portType.rawValue)
        }
        // Audio route names are not asserted to identify the video receiver.
        return GuestScreenMetadata(source: source,
            sizePoints: GuestScreenSize(width: Double(bounds.width), height: Double(bounds.height)),
            nativePixels: GuestScreenSize(width: Double(native.width), height: Double(native.height)),
            scale: Double(screen.scale), nativeScale: Double(screen.nativeScale),
            availableModes: screen.availableModes.map(mode),
            currentMode: screen.currentMode.map(mode), preferredMode: screen.preferredMode.map(mode),
            maximumFramesPerSecond: screen.maximumFramesPerSecond,
            overscanInsetsPoints: GuestScreenInsets(top: Double(insets.top), left: Double(insets.left),
                bottom: Double(insets.bottom), right: Double(insets.right)),
            audioRoutes: routes,
            colorGamut: traits.displayGamut == .P3 ? "P3" : (traits.displayGamut == .SRGB ? "sRGB" : nil),
            currentEDRHeadroom: finite(screen.currentEDRHeadroom),
            potentialEDRHeadroom: finite(screen.potentialEDRHeadroom))
    }

    private static func finite(_ value: CGFloat) -> Double? {
        value.isFinite ? Double(value) : nil
    }
}

/// Owns external presentation, while the original Metal view remains the
/// runtime's surface and the phone's touch/keyboard preview.
@MainActor
final class GuestExternalDisplay: ObservableObject {
    static let shared = GuestExternalDisplay()
    @Published private(set) var external: GuestDisplayRequest?
    @Published var error: String?
    private weak var surface: GuestExternalVideoView?
    private var phone: GuestDisplayRequest?
    private var running = false
    private let subscription = UUID()

    func phoneDidLayout(_ view: GuestMetalView) {
        guard let screen = view.window?.screen,
              let geometry = GuestDisplayLayout(scale: view.scale, points: view.bounds.size,
                                                nativeScale: Double(screen.nativeScale)) else { return }
        let request = GuestDisplayRequest(width: geometry.width, height: geometry.height,
            metadata: GuestScreenFacts.metadata(screen: screen, bounds: view.bounds,
                                               traits: view.traitCollection, source: "built-in"))
        guard phone != request else { return }
        phone = request
        // Normal phone-only boots retain the existing runtime auto-resize
        // policy. After an external session, explicitly restore its geometry.
        if external == nil, hasUsedExternalDisplay { GuestDisplayConfiguration.shared.configure(request) }
    }

    private var hasUsedExternalDisplay = false

    func connect(_ request: GuestDisplayRequest, surface: GuestExternalVideoView) {
        do { _ = try request.validated() }
        catch { self.error = error.localizedDescription; return }
        self.surface = surface
        setExternal(request)
        refreshFrames()
    }

    /// Also used by callers that supply an already measured external geometry.
    func setExternal(_ request: GuestDisplayRequest?) {
        if let request {
            do { _ = try request.validated() }
            catch { self.error = error.localizedDescription; return }
            hasUsedExternalDisplay = true
        }
        guard external != request else { return }
        external = request
        error = nil
        if let desired = request ?? phone { GuestDisplayConfiguration.shared.configure(desired) }
    }

    func disconnect(_ surface: GuestExternalVideoView) {
        guard self.surface === surface else { return }
        self.surface = nil
        GuestFrameStream.shared.unsubscribe(id: subscription)
        setExternal(nil)
    }

    func guestRunningChanged(_ running: Bool) {
        self.running = running
        GuestDisplayConfiguration.shared.guestRunningChanged(running)
        if running, let desired = external ?? (hasUsedExternalDisplay ? phone : nil) {
            GuestDisplayConfiguration.shared.configure(desired)
        }
        refreshFrames()
    }

    func retry() {
        error = nil
        GuestDisplayConfiguration.shared.retry()
        refreshFrames()
    }

    private func refreshFrames() {
        guard running, let surface, let external else {
            GuestFrameStream.shared.unsubscribe(id: subscription)
            return
        }
        let result = GuestFrameStream.shared.subscribe(id: subscription,
            width: UInt32(external.width), fps: 30) { [weak surface] buffer in surface?.show(buffer) }
        if result != 0 { error = "The runtime could not provide external display frames (\(result))." }
    }
}

@MainActor
final class GuestExternalVideoView: UIView {
    override class var layerClass: AnyClass { AVSampleBufferDisplayLayer.self }
    private var feed: (any GuestVideoFeed)?
    private var format: CMVideoFormatDescription?
    private(set) var frameSize = CGSize.zero
    private(set) var framesShown = 0

    override init(frame: CGRect) {
        super.init(frame: frame)
        let layer = self.layer as! AVSampleBufferDisplayLayer
        layer.videoGravity = .resizeAspect
        layer.backgroundColor = UIColor.black.cgColor
        feed = makeGuestVideoFeed(layer: layer)
        feed?.prepare()
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func show(_ buffer: CVPixelBuffer) {
        let size = CGSize(width: CVPixelBufferGetWidth(buffer), height: CVPixelBufferGetHeight(buffer))
        if format == nil || size != frameSize {
            var description: CMVideoFormatDescription?
            guard CMVideoFormatDescriptionCreateForImageBuffer(allocator: kCFAllocatorDefault,
                imageBuffer: buffer, formatDescriptionOut: &description) == noErr else { return }
            format = description
            frameSize = size
        }
        guard let format, let feed else { return }
        var timing = CMSampleTimingInfo(duration: .invalid,
            presentationTimeStamp: CMClockGetTime(CMClockGetHostTimeClock()), decodeTimeStamp: .invalid)
        var sample: CMSampleBuffer?
        guard CMSampleBufferCreateReadyWithImageBuffer(allocator: kCFAllocatorDefault, imageBuffer: buffer,
            formatDescription: format, sampleTiming: &timing, sampleBufferOut: &sample) == noErr,
            let sample else { return }
        feed.enqueue(sample)
        framesShown += 1
    }
}

@MainActor
final class GuestExternalViewController: UIViewController {
    let surface = GuestExternalVideoView(frame: .zero)
    private var routeObservation: AnyCancellable?
    override func loadView() { view = surface }
    override func viewDidLoad() {
        super.viewDidLoad()
        routeObservation = NotificationCenter.default.publisher(for: AVAudioSession.routeChangeNotification)
            .receive(on: RunLoop.main).sink { [weak self] _ in
                MainActor.assumeIsolated { self?.view.setNeedsLayout() }
            }
    }
    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        guard let screen = view.window?.windowScene?.screen else { return }
        let size = view.bounds.size
        let request = GuestDisplayRequest(width: Int((size.width * screen.scale).rounded(.down)),
            height: Int((size.height * screen.scale).rounded(.down)),
            metadata: GuestScreenFacts.metadata(screen: screen, bounds: view.bounds,
                                               traits: traitCollection, source: "external"))
        GuestExternalDisplay.shared.connect(request, surface: surface)
    }
}

@MainActor
final class GuestExternalSceneDelegate: NSObject, UIWindowSceneDelegate {
    var window: UIWindow?
    private var controller: GuestExternalViewController?

    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options connectionOptions: UIScene.ConnectionOptions) {
        guard let scene = scene as? UIWindowScene,
              session.role == .windowExternalDisplayNonInteractive else { return }
        let controller = GuestExternalViewController()
        let window = UIWindow(windowScene: scene)
        window.rootViewController = controller
        self.controller = controller
        self.window = window
        window.isHidden = false
    }

    func sceneDidDisconnect(_ scene: UIScene) {
        if let controller { GuestExternalDisplay.shared.disconnect(controller.surface) }
        window?.isHidden = true
        window = nil
        controller = nil
    }
}

struct GuestExternalDisplayAnchor: UIViewControllerRepresentable {
    func makeUIViewController(context: Context) -> GuestExternalAccessoryController { GuestExternalAccessoryController() }
    func updateUIViewController(_ controller: GuestExternalAccessoryController, context: Context) {}
}

@MainActor
final class GuestExternalAccessoryController: UIViewController {
    // Retain the registration without requiring its iOS 27 type on iOS 26.
    private var registration: AnyObject?
    override func viewDidLoad() {
        super.viewDidLoad()
        if #available(iOS 27.0, *) {
            let configuration = UISceneConfiguration(name: "Guest external display", sessionRole: .windowExternalDisplayNonInteractive)
            configuration.delegateClass = GuestExternalSceneDelegate.self
            registration = registerSceneAccessory(.externalNonInteractive(sceneConfiguration: configuration))
        }
    }
}
