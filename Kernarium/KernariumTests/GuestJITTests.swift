// SPDX-License-Identifier: GPL-2.0-or-later

import Foundation
import Testing
@testable import Kernarium

@MainActor
@Suite(.serialized)
struct GuestJITTests {
    @Test
    func onlyEnabledAndSuccessfullyPreparedJITReachesTheBootRequest() {
        let answers: [Int] = [KERNARIUM_RUNTIME_JIT_READY, KERNARIUM_RUNTIME_JIT_UNSUPPORTED,
                                KERNARIUM_RUNTIME_JIT_NO_DEBUGGER, KERNARIUM_RUNTIME_JIT_FAILED,
                                KERNARIUM_RUNTIME_JIT_OTHER_SETTINGS, KERNARIUM_RUNTIME_JIT_DISABLED, 99]
        for answer in answers {
            var request = kernarium_runtime_boot()
            let choice = GuestJITChoice(enabled: true, answer: answer)
            choice.apply(to: &request)
            #expect(request.use_jit == (answer == KERNARIUM_RUNTIME_JIT_READY ? 1 : 0))
            GuestJITChoice(enabled: false, answer: answer).apply(to: &request)
            #expect(request.use_jit == 0)
        }
    }

    @Test
    func jitPreferencePersistsAndLaunchOverrideIsExplicit() throws {
        let name = "GuestJITTests.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        var configuration = EmulatorConfiguration.load(defaults: defaults, physicalProcessorCount: 2, environment: [:])
        #expect(configuration.jitEnabled)
        configuration.jitEnabled = false
        configuration.save(defaults: defaults)
        #expect(!EmulatorConfiguration.load(defaults: defaults, physicalProcessorCount: 2, environment: [:]).jitEnabled)
        #expect(EmulatorConfiguration.load(defaults: defaults, physicalProcessorCount: 2, environment: ["KERNARIUM_JIT": "1"]).jitEnabled)
        #expect(!EmulatorConfiguration.load(defaults: defaults, physicalProcessorCount: 2, environment: ["KERNARIUM_JIT": "0"]).jitEnabled)
    }

    #if targetEnvironment(simulator)
    @Test
    func actualSimulatorRuntimeReportsUnavailableAndKeepsTheGadgetEngine() async throws {
        try #require(kernarium_runtime_loader_select(UInt32(KERNARIUM_RUNTIME_GUEST_LINUX)) == 0)
        let choice = await Task.detached {
            GuestJITChoice.prepare(enabled: true, memoryMiB: 512, vCPUs: 1)
        }.value
        #expect(choice.answer == KERNARIUM_RUNTIME_JIT_UNSUPPORTED)
        #expect(!choice.usesJIT)
        print("JIT_PROOF: actual Simulator runtime preparation=\(choice.answer); \(choice.status)")
    }
    #endif
}
