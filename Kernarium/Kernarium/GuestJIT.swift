// SPDX-License-Identifier: GPL-2.0-or-later

import Foundation

/// A preference asks for JIT; only the runtime's successful preparation
/// authorizes selecting that engine for the boot's memory and vCPU count.
nonisolated struct GuestJITChoice: Equatable, Sendable {
    let enabled: Bool
    let answer: Int

    var usesJIT: Bool { enabled && answer == KERNARIUM_RUNTIME_JIT_READY }

    var status: String {
        if usesJIT { return "CPU engine: JIT" }
        if !enabled { return "CPU engine: Gadgets (JIT is off)" }
        switch answer {
        case KERNARIUM_RUNTIME_JIT_NO_DEBUGGER:
            return "CPU engine: Gadgets (JIT needs a servicing debugger)"
        case KERNARIUM_RUNTIME_JIT_OTHER_SETTINGS:
            return "CPU engine: Gadgets (reopen the app to prepare JIT for the new CPU or RAM settings)"
        case KERNARIUM_RUNTIME_JIT_UNSUPPORTED:
            return "CPU engine: Gadgets (JIT is unavailable in this runtime)"
        default:
            return "CPU engine: Gadgets (JIT preparation failed)"
        }
    }

    func apply(to request: inout kernarium_runtime_boot) {
        request.use_jit = usesJIT ? 1 : 0
    }

    /// Called on the launch thread, before entering the guest runtime.
    static func prepare(enabled: Bool, memoryMiB: UInt32, vCPUs: UInt32) -> Self {
        #if DEBUG
        if enabled, kernarium_runtime_loader_debugger_attached() != 0,
           ProcessInfo.processInfo.environment["KERNARIUM_JIT_DEBUGGER"] != "1" {
            // Xcode's debugger does not serve the region preparation protocol.
            return Self(enabled: true, answer: KERNARIUM_RUNTIME_JIT_NO_DEBUGGER)
        }
        #endif
        let answer = kernarium_runtime_loader_prepare_jit(enabled ? 1 : 0, memoryMiB, vCPUs)
        return Self(enabled: enabled, answer: Int(answer))
    }
}
