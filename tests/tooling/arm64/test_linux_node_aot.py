#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Linux Node windows preserve instruction addresses and immutable file bytes."""
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools/aot'))
import elf64
import linux_node


class Image:
    data = bytes(16384)
    sha256 = 'ab' * 32

    def executable_segment(self):
        return elf64.Segment(4096, 4096, 12288, 12288, 5)

    def functions(self):
        return [elf64.Function(4096, 12288), elf64.Function(12288, 16384)]


class Windows(unittest.TestCase):
    def test_capsule_payload_is_accessible_to_guest_users_with_a_private_umask(self):
        class CapsuleChecked(Exception):
            pass

        with tempfile.TemporaryDirectory() as temporary:
            scratch = Path(temporary)
            archive = scratch / 'node.tar.xz'
            with tarfile.open(archive, 'w:xz') as destination:
                for relative, content, mode in (
                        ('bin/node', b'fixture', 0o755), ('LICENSE', b'fixture', 0o644),
                        ('lib/node_modules/npm/package.json', json.dumps({'name': 'npm', 'version': '10.9.2'}).encode(), 0o644),
                        ('lib/node_modules/npm/bin/npm-cli.js', b'fixture', 0o755),
                        ('lib/node_modules/npm/lib/cli.js', b'fixture', 0o644)):
                    member = tarfile.TarInfo(f'node-v{linux_node.VERSION}-linux-arm64/{relative}')
                    member.size = len(content)
                    member.mode = mode
                    destination.addfile(member, io.BytesIO(content))

            def inspect_payload(command, **kwargs):
                root = Path(command[-1])
                for relative in ('', 'bin', 'bin/node', 'lib', 'lib/node_modules',
                                 'lib/node_modules/npm', 'lib/node_modules/npm/bin/npm-cli.js'):
                    permissions = stat.S_IMODE((root / relative).stat().st_mode)
                    self.assertEqual(permissions, 0o755, relative)
                for relative in ('LICENSE', 'identity.json', 'lib/node_modules/npm/package.json',
                                 'lib/node_modules/npm/lib/cli.js'):
                    self.assertEqual(stat.S_IMODE((root / relative).stat().st_mode), 0o644, relative)
                self.assertEqual(json.loads((root / 'identity.json').read_text())['npm_version'], '10.9.2')
                raise CapsuleChecked

            previous = os.umask(0o077)
            try:
                with mock.patch.object(linux_node.artifact, 'sha256_file', return_value=linux_node.ARCHIVE_SHA256), \
                        mock.patch.object(linux_node.subprocess, 'run', side_effect=inspect_payload):
                    with self.assertRaises(CapsuleChecked):
                        linux_node.build(archive, scratch / 'output', Path(__file__).resolve().parents[3])
            finally:
                os.umask(previous)

    def test_embedded_builtin_text_does_not_need_unwind_tables(self):
        image = Image()
        image.functions = lambda: []
        window = elf64.ExecutableWindow(image, 4096, 12288, True)
        self.assertEqual(window.functions(), [elf64.Function(4096, 8192),
                                              elf64.Function(8192, 12288)])

    def test_crossing_function_keeps_both_halves_at_original_addresses(self):
        first = elf64.ExecutableWindow(Image(), 4096, 8192)
        second = elf64.ExecutableWindow(Image(), 8192, 16384)
        self.assertEqual(first.functions(), [elf64.Function(4096, 8192)])
        self.assertEqual(second.functions(), [elf64.Function(8192, 12288), elf64.Function(12288, 16384)])
        self.assertEqual(second.executable_segment().offset, 8192)
        self.assertIs(first.data, Image.data)

    def test_invalid_or_outside_windows_are_refused(self):
        for begin, end in ((4097, 8192), (4096, 8193), (8192, 4096), (0, 4096), (4096, 20480)):
            with self.subTest(begin=begin, end=end), self.assertRaises(elf64.ElfError):
                elf64.ExecutableWindow(Image(), begin, end)

    def test_mismatched_capsule_is_rejected_before_installing_it(self):
        with tempfile.TemporaryDirectory() as scratch:
            output = Path(scratch) / 'input'
            output.mkdir()
            (output / 'node.erofs').write_bytes(b'changed capsule')
            root = Path(scratch) / 'root'
            with mock.patch.object(linux_node, 'verify', return_value={
                    'release': {'system_partition_sha256': '00' * 32}}):
                with self.assertRaisesRegex(ValueError, 'capsule differs'):
                    linux_node.install(output, root)
            self.assertFalse(root.exists())


if __name__ == '__main__':
    unittest.main()
