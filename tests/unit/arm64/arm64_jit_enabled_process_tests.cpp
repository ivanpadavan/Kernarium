// SPDX-License-Identifier: GPL-2.0-or-later
#include "cpu/jit_memory/executable_memory.hpp"
#include "cpu/jit_memory/executable_memory_testing.hpp"
#include "require.hpp"

#include <cstdint>
#include <cstdio>
#include <exception>

namespace {
namespace memory = uml::cpu::jit_memory;
namespace hooks = memory::testing;
using uml::testing::require;
constexpr std::size_t granule = 16U * 1024U;
constexpr std::uint32_t debugged = 0x10000000U;
constexpr std::uint32_t kill = 0x00000200U;
bool absent() noexcept { return false; }
}

int main()
{
	try {
		hooks::set_traced_probe(absent);
		hooks::send_real_requests(true);
		hooks::set_code_signing_flags(0);
		require(memory::prepare_debugger_region(8 * granule) ==
			memory::RegionStatus::no_debugger,
			"an ordinary untraced process acquired executable memory");
		hooks::set_code_signing_flags(debugged | kill);
		require(memory::prepare_debugger_region(8 * granule) ==
			memory::RegionStatus::no_debugger,
			"a process that still kills invalid pages was probed");
		require(!memory::available(), "an unprepared region was exposed");
		hooks::set_code_signing_flags(debugged);
		require(memory::prepare_debugger_region(8 * granule) ==
			memory::RegionStatus::ready,
			"existing JIT permission did not prepare the region");
		require(memory::available(), "the enabled process has no executable memory");
		require(hooks::unanswered_requests_seen() == 0,
			"the enabled process sent a breakpoint to an absent debugger");
		for (std::uint32_t index = 0; index < 8; ++index) {
			void *code = memory::allocate(granule);
			require(code != nullptr, "a verified granule was not allocated");
			auto *words = static_cast<std::uint32_t *>(memory::writable_alias(code));
			const auto expected = 0x1200U + index;
			words[0] = 0x52800000U | (expected << 5U);
			words[1] = 0xd65f03c0U;
			memory::invalidate_instruction_cache(code, 8);
			require(reinterpret_cast<std::uint32_t (*)()>(code)() == expected,
				"code written through a granule's alias did not execute");
		}
		std::puts("Already-enabled JIT process: every granule executed without a debugger");
		return 0;
	} catch (const std::exception& error) {
		std::fprintf(stderr, "Already-enabled JIT process test failed: %s\n", error.what());
		return 1;
	}
}
