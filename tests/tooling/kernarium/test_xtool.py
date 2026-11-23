#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cross-packaged frameworks preserve their platform and original debug files."""

import importlib.util
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('apple_tools', ROOT / 'tools/ci/xtool/apple-tools.py')
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)


class Packaging(unittest.TestCase):
    def package(self, scratch, platform='2', architecture='arm64'):
        source = scratch / 'Probe.framework'
        source.mkdir()
        (source / 'Probe').write_bytes(b'original framework bytes')
        symbols = scratch / 'Probe.framework.dSYM'
        symbols.mkdir()
        (symbols / 'original').write_bytes(b'original debug bytes')
        output = scratch / 'Probe.xcframework'

        def capture(command, **kwargs):
            return architecture if '-archs' in command else f'Load command 1\n cmd LC_BUILD_VERSION\n platform {platform}\n'

        with mock.patch.object(tools, 'llvm_tool', side_effect=lambda name: name), \
                mock.patch.object(tools.subprocess, 'check_output', side_effect=capture):
            tools.create_xcframework(['-framework', str(source), '-debug-symbols', str(symbols),
                                     '-output', str(output)])
        return source, output

    def test_simulator_keeps_its_variant_and_debug_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output = self.package(Path(temporary), '7')
            metadata = plistlib.loads((output / 'Info.plist').read_bytes())
            entry, = metadata['AvailableLibraries']
            self.assertEqual(entry['SupportedPlatformVariant'], 'simulator')
            directory = output / entry['LibraryIdentifier']
            self.assertEqual((directory / entry['LibraryPath'] / 'Probe').read_bytes(),
                             (source / 'Probe').read_bytes())
            self.assertEqual((directory / entry['DebugSymbolsPath'] / 'Probe.framework.dSYM/original').read_bytes(),
                             b'original debug bytes')

    def test_mixed_device_and_simulator_archive_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(ValueError, 'mixed Mach-O'):
            self.package(Path(temporary), '2\n platform 7')

    def test_unexpected_architecture_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(ValueError, 'Expected arm64'):
            self.package(Path(temporary), architecture='arm64 x86_64')

    def test_adapter_cannot_run_an_xcode_build(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported tool invocation'):
            tools.main('xcodebuild', ['build'])


if __name__ == '__main__':
    unittest.main()
